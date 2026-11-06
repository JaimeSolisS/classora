"""What each demo account sees of the M8 data (#539 D1-D28) on the whole demo
database at ``FROZEN_NOW``.

Three tests, one full run each: the reads (approvals, notification
preferences, the audit log, business settings and payment methods, imports,
saved setups, KPIs, dashboards, failed jobs and recently deleted), then the
writes on their own databases (exports and their replay, a dashboard layout
and a restore; then the retention run 31 days later), so nothing persists on
a demo database. Figures are identities, presence and "at least one" (E4);
no money or KPI figure is pinned."""

import asyncio
import json
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.db import create_sessionmaker
from app.core.money import Money
from app.modules.administration.retention import protected_tables, run_retention_with
from tests.factories import demo
from tests.factories.demo_admin import (
    BALANCES_FILE,
    DELETED_PLAN_TITLE,
    FINANCE_SETUP_NAME,
    RECEIPT_MESSAGE_ES,
    SCHEDULES_FILE,
    STUDENTS_FILE,
    TUTORS_FILE,
    plan_demo_admin,
)
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.catalog_support import get, patch, post
from tests.integration.conftest import run_sql, short_lived_engine
from tests.integration.demo_support import FROZEN_NOW, count, demo_env, migrate
from tests.integration.export_support import run_job
from tests.integration.invitation_support import REAUTHENTICATE
from tests.integration.test_demo_schedule import PASSWORDS, signed_in

T = date(2026, 11, 4)
DEFAULT_RANGE = (T - timedelta(days=89), T)
""""Últimos 3 meses" as ``rules.ts`` computes it: 90 days ending today."""
TWO_WEEKS = (T - timedelta(days=13), T)
"""E6's custom range (previous window ``T − 27 … T − 14``)."""

STAFF = ("demo.owner", "demo.admin", "demo.coordinator", "demo.finance")
FAMILY_AND_TUTOR = ("demo.tutor", "demo.student", "demo.guardian")
EVERYONE = (*STAFF, *FAMILY_AND_TUTOR)

KPI = "/api/v1/reports/kpis"
KPIS = {
    "K1": f"{KPI}/revenue-trend",
    "K2": f"{KPI}/course-profitability",
    "K4": f"{KPI}/room-utilization",
    "K5": f"{KPI}/student-retention",
    "K6": f"{KPI}/tutor-performance",
}
KPI_ACCESS = {
    "demo.owner": {"K1", "K2", "K4", "K5", "K6"},
    "demo.finance": {"K1", "K2", "K4", "K5"},
    "demo.admin": {"K4", "K5", "K6"},
    "demo.coordinator": {"K4", "K5", "K6"},
    "demo.tutor": set(),
    "demo.student": set(),
    "demo.guardian": set(),
}
"""D17."""
PROFITABILITY = "/api/v1/reports/finance/profitability"
BALANCES = "/api/v1/reports/finance/balances"
LAYOUT = "/api/v1/reports/dashboard/layout"
ALL_WIDGETS = (
    "revenue_trend",
    "balances",
    "course_profitability",
    "room_utilization",
    "student_retention",
    "tutor_performance",
)
WIDGETS = {
    "demo.owner": ALL_WIDGETS,
    "demo.finance": ALL_WIDGETS[:5],
    "demo.admin": ALL_WIDGETS[3:],
    "demo.coordinator": ALL_WIDGETS[3:],
}
"""D20: the role defaults, in catalog order."""
WIDGET_ROUTES = {
    "revenue_trend": KPIS["K1"],
    "balances": BALANCES,
    "course_profitability": KPIS["K2"],
    "room_utilization": KPIS["K4"],
    "student_retention": KPIS["K5"],
    "tutor_performance": KPIS["K6"],
}
STAFF_ONLY_TYPES = {
    "administration.approval_assigned",
    "reporting.export_ready",
    "reporting.export_failed",
    "scheduling.offer_declined",
    "scheduling.offer_expired",
    "scheduling.reassignment_needed",
}
"""The coordinator's catalogue: staff notices only (D3)."""
APPROVALS = "/api/v1/approval-requests"
IMPORTS = "/api/v1/imports"
EXPORTS = "/api/v1/exports"
SETUPS = "/api/v1/report-setups"
DELETED = "/api/v1/deleted-records"
JOBS = "/api/v1/jobs"


@pytest.fixture
def migrated_url(fresh_database_url: str) -> str:
    return migrate(fresh_database_url)


@pytest.fixture
def env_for(monkeypatch):
    return lambda app_env, dsn: demo_env(monkeypatch, app_env, dsn)


def _seeded(url: str, env_for: Any, clock: Any) -> None:
    clock.set(FROZEN_NOW)
    env_for("local", url)
    assert demo.main([]) == 0


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json() if response.content else None


def _status(response: Any) -> int:
    return int(response.status_code)


def _code(response: Any) -> str:
    return str(response.json().get("code"))


def _cookies(client: TestClient) -> dict[str, Any]:
    """One sign-in per demo account."""
    return {username: signed_in(client, username) for username in EVERYONE}


def _ranged(start: date, end: date) -> dict[str, str]:
    return {"from": start.isoformat(), "to": end.isoformat()}


def _one(url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    [row] = run_sql(url, sql, params)
    return row


def _user(url: str, username: str) -> str:
    return str(
        _one(url, "SELECT id FROM users WHERE username = :u", {"u": username}).id
    )


def _qualification_item(url: str) -> str:
    return str(
        _one(
            url,
            "SELECT id FROM approval_requests WHERE request_type ="
            " 'staff.qualification' AND status = 'pending'",
        ).id
    )


def _jobs(url: str) -> dict[str, str]:
    return {
        r.file_name: str(r.id)
        for r in run_sql(url, "SELECT id, file_name FROM import_jobs")
    }


def _deleted_plan(url: str) -> Any:
    return _one(
        url,
        "SELECT id, version FROM learning_plans WHERE title = :t",
        {"t": DELETED_PLAN_TITLE},
    )


def _put(client: TestClient, cookie: Any, path: str, body: Any) -> Any:
    client.cookies.clear()
    return client.put(path, json=body, headers=signed_headers(cookie))


def _delete(client: TestClient, cookie: Any, path: str) -> Any:
    client.cookies.clear()
    return client.delete(path, headers=signed_headers(cookie))


def _reauthenticate(client: TestClient, cookie: Any, username: str) -> None:
    client.cookies.clear()
    response = client.post(
        REAUTHENTICATE,
        json={"password": PASSWORDS[username]},
        headers=signed_headers(cookie),
    )
    assert response.status_code == 200, response.text


def test_every_account_reads_its_m8_data(migrated_url, env_for, clock):
    _seeded(migrated_url, env_for, clock)
    url = migrated_url
    item = _qualification_item(url)
    admin_id, owner_id = _user(url, "demo.admin"), _user(url, "demo.owner")
    jobs = _jobs(url)
    plan = _deleted_plan(url)

    with auth_client(url) as client:
        cookies = _cookies(client)

        def read(who: str, path: str, **params: Any) -> Any:
            return get(client, cookies[who], path, **params)

        # D1, D2 (#118): demo.admin's assigned item; the families see none.
        mine = _ok(read("demo.admin", APPROVALS, assignee="me"))
        assert [i["id"] for i in mine["items"]] == [item]
        for who in FAMILY_AND_TUTOR:
            response = read(who, APPROVALS, assignee="me")
            assert _ok(response)["items"] == [], who
            options = read(who, f"{APPROVALS}/{item}/assignee-options")
            assert _status(options) == 404, who
        # B2: no open item is high at FROZEN_NOW, so the high filter is empty;
        # at least one open item stays unassigned.
        assert _ok(read("demo.owner", APPROVALS, priority="high"))["items"] == []
        everything = _ok(read("demo.owner", APPROVALS, limit=100))["items"]
        assert any(i["assignee"] is None for i in everything)

        # D3 (#119): the mute, and each catalogue's types.
        preferences = {
            who: {
                i["notification_type"]: i["enabled"]
                for i in _ok(read(who, "/api/v1/notifications/preferences"))["items"]
            }
            for who in ("demo.coordinator", "demo.admin", "demo.guardian", "demo.tutor")
        }
        assert (
            preferences["demo.coordinator"]["administration.approval_assigned"] is False
        )
        assert preferences["demo.admin"]["administration.approval_assigned"] is True
        assert not set(preferences["demo.guardian"]) & STAFF_ONLY_TYPES
        assert not set(preferences["demo.tutor"]) & STAFF_ONLY_TYPES
        assert "scheduling.offer_received" in preferences["demo.tutor"]
        assert "scheduling.offer_received" not in preferences["demo.guardian"]

        # D4-D6 (#120): the audit log.
        for who in ("demo.owner", "demo.admin"):
            assert _ok(read(who, "/api/v1/audit-events"))["items"], who
        for who in (*STAFF[2:], *FAMILY_AND_TUTOR):
            assert _status(read(who, "/api/v1/audit-events")) == 403, who
        trail = _ok(
            read(
                "demo.owner",
                "/api/v1/audit-events",
                record_type="approval_requests",
                record_id=item,
            )
        )["items"]
        [assigned] = [
            e
            for e in trail
            if e["action"] == "update" and "assignee_user_id" in (e["changes"] or {})
        ]
        assert assigned["changes"]["assignee_user_id"]["new"] == admin_id
        assert assigned["actor"]["id"] == owner_id
        assert admin_id in {a["id"] for a in assigned["accounts"]}
        assert any(e["actor"] is None for e in trail)  # the system-created item
        for record_type in ("compensation_rates", "expenses"):
            record = str(
                _one(url, f"SELECT id FROM {record_type} LIMIT 1").id  # noqa: S608
            )
            for who in ("demo.admin", "demo.owner"):
                events = _ok(
                    read(
                        who,
                        "/api/v1/audit-events",
                        record_type=record_type,
                        record_id=record,
                    )
                )["items"]
                assert events, (record_type, who)
                assert "[masked]" in json.dumps(events), (record_type, who)

        # D7 (#121): payment methods and the message.
        for who in ("demo.owner", "demo.admin"):
            [other] = [
                m
                for m in _ok(read(who, "/api/v1/billing/payment-methods"))
                if m["code"] == "other"
            ]
            path = "/api/v1/billing/payment-methods/other"
            body = {"is_active": True, "version": other["version"]}
            assert _status(patch(client, cookies[who], path, body)) == 200, who
            off = patch(client, cookies[who], path, {**body, "is_active": False})
            assert (_status(off), _code(off)) == (403, "reauth_required"), who
        denied = patch(
            client,
            cookies["demo.finance"],
            "/api/v1/billing/payment-methods/other",
            {"is_active": True, "version": 1},
        )
        assert (_status(denied), _code(denied)) == (403, "permission_denied")
        settings = _ok(read("demo.guardian", "/api/v1/business-settings"))
        assert settings["receipt_message_es"] == RECEIPT_MESSAGE_ES

        # D9, D10 (#122, #123): who sees which job; the rows.
        def listed(who: str) -> set[str]:
            return {j["id"] for j in _ok(read(who, IMPORTS, limit=100))["items"]}

        assert listed("demo.admin") == listed("demo.owner") == set(jobs.values())
        for who, visible in (
            ("demo.coordinator", STUDENTS_FILE),
            ("demo.finance", BALANCES_FILE),
        ):
            assert listed(who) == {jobs[visible]}, who
            for name, job_id in jobs.items():
                expected = 200 if name == visible else 404
                assert _status(read(who, f"{IMPORTS}/{job_id}")) == expected
        for who in FAMILY_AND_TUTOR:
            assert _status(read(who, IMPORTS)) == 403, who
        for name in (STUDENTS_FILE, SCHEDULES_FILE, BALANCES_FILE):
            rows = _ok(read("demo.admin", f"{IMPORTS}/{jobs[name]}/rows"))["items"]
            assert rows and {r["values"] is None for r in rows} == {True}, name
        staged = _ok(read("demo.admin", f"{IMPORTS}/{jobs[TUTORS_FILE]}/rows"))
        assert all(r["values"] is not None for r in staged["items"])
        f3 = _one(
            url,
            "SELECT DISTINCT fs.family_id FROM charges c"
            " JOIN family_students fs ON fs.student_id = c.student_id"
            " WHERE c.import_job_id IS NOT NULL",
        ).family_id
        summary = _ok(read("demo.finance", f"/api/v1/families/{f3}/billing-account"))
        charges = _ok(
            read(
                "demo.finance",
                f"/api/v1/families/{f3}/billing-account/charges",
                limit=100,
            )
        )["items"]
        assert Money(summary["outstanding"]) == Money.total(
            Money(c["open"]) for c in charges
        )
        imported = {
            str(r.id)
            for r in run_sql(
                url, "SELECT id FROM charges WHERE import_job_id IS NOT NULL"
            )
        }
        assert len(imported) == 2
        assert imported <= {c["id"] for c in charges if Money(c["open"]) > Money.zero()}
        [series] = [
            str(r.record_id)
            for r in run_sql(
                url,
                "SELECT r.record_id FROM import_rows r JOIN import_jobs j"
                " ON j.id = r.job_id WHERE j.file_name = :f AND r.status = 'created'",
                {"f": SCHEDULES_FILE},
            )
        ]
        offers = _ok(read("demo.tutor", "/api/v1/assignment-offers", status="pending"))
        assert series in {(o["series"] or {}).get("id") for o in offers["items"]}

        # D15 (#125): who sees which setup.
        def setups(who: str) -> list[dict[str, Any]]:
            return list(_ok(read(who, SETUPS))["items"])

        owner_setups = setups("demo.owner")
        assert [(s["name"], s["mine"], s["can_run"]) for s in owner_setups] == [
            (FINANCE_SETUP_NAME, False, True)
        ]
        assert [s["mine"] for s in setups("demo.admin")] == [True]
        assert setups("demo.coordinator") == []
        assert [(s["name"], s["mine"]) for s in setups("demo.finance")] == [
            (FINANCE_SETUP_NAME, True)
        ]
        for who in FAMILY_AND_TUTOR:
            assert _status(read(who, SETUPS)) == 403, who

        # D17-D19 (#126): access, then the identities over the default range.
        for who, allowed in KPI_ACCESS.items():
            for code, path in KPIS.items():
                params = _ranged(*DEFAULT_RANGE)
                if code == "K1":
                    params["basis"] = "accrual"
                expected = 200 if code in allowed else 403
                assert _status(read(who, path, **params)) == expected, (who, code)
        _kpi_identities(lambda path, **params: read("demo.owner", path, **params))

        # D20, D21 (#127): the role defaults; each widget's route.
        for who, widgets in WIDGETS.items():
            layout = _ok(read(who, LAYOUT))
            assert layout["customized"] is False, who
            assert [w["widget"] for w in layout["widgets"]] == list(widgets), who
            assert all(w["visible"] for w in layout["widgets"]), who
            for widget, path in WIDGET_ROUTES.items():
                params = {} if widget == "balances" else _ranged(*DEFAULT_RANGE)
                if widget == "revenue_trend":
                    params["basis"] = "accrual"
                expected = 200 if widget in widgets else 403
                assert _status(read(who, path, **params)) == expected, (who, widget)
        body = {"widgets": [{"widget": "room_utilization", "visible": True}]}
        for who in FAMILY_AND_TUTOR:
            assert _status(read(who, LAYOUT)) == 403, who
            assert _status(_put(client, cookies[who], LAYOUT, body)) == 403, who
            assert _status(_delete(client, cookies[who], LAYOUT)) == 403, who

        # D23-D25 (#128): failed jobs.
        for who in ("demo.owner", "demo.admin"):
            me = _ok(read(who, "/api/v1/auth/me"))
            assert "jobs.manage" in json.dumps(me), who
            assert _ok(read(who, JOBS))["items"] == [], who
            assert _status(read(who, f"{JOBS}/tasks")) == 200, who
        for who in (*STAFF[2:], *FAMILY_AND_TUTOR):
            assert _status(read(who, JOBS)) == 403, who
            assert _status(read(who, f"{JOBS}/tasks")) == 403, who
        retry = post(client, cookies["demo.admin"], f"{JOBS}/999999999/retry", {})
        assert _status(retry) == 404, retry.text

        # D26 (list), D27 (#129): only demo.tutor lists the draft.
        [listed_plan] = _ok(read("demo.tutor", DELETED))["items"]
        assert (listed_plan["kind"], listed_plan["id"], listed_plan["title"]) == (
            "learning_plan",
            str(plan.id),
            DELETED_PLAN_TITLE,
        )
        restore = f"{DELETED}/learning_plan/{plan.id}/restore"
        for who in ("demo.admin", "demo.finance", "demo.student", "demo.guardian"):
            assert _ok(read(who, DELETED))["items"] == [], who
            denied = post(client, cookies[who], restore, {"version": plan.version})
            assert _status(denied) == 404, (who, denied.text)

    # Nothing above wrote a layout, an export or a job.
    for table in ("dashboard_layouts", "export_jobs", "procrastinate_jobs"):
        assert count(url, table) == 0, table


def _kpi_identities(read: Any) -> None:
    """D18 over the default range, D19 over E6's two weeks (as ``demo.owner``)."""
    default = _ranged(*DEFAULT_RANGE)
    reports: dict[str, Any] = {}
    for basis in ("accrual", "cash"):
        trend = _ok(read(KPIS["K1"], basis=basis, **default))
        report = reports[basis] = _ok(read(PROFITABILITY, basis=basis, **default))
        fields = (
            "revenue_total",
            "direct_tutor_cost_total",
            "gross_profit",
            "net_result",
        )
        for field in fields:
            months = Money.total(Money(m[field]) for m in trend["months"])
            assert months == Money(trend["totals"][field]) == Money(report[field]), (
                basis,
                field,
            )
        assert Money(trend["totals"]["operating_expenses_total"]) == Money(
            report["operating_expenses"]["total"]
        )
        if basis == "accrual":
            assert Money(trend["totals"]["revenue_total"]) > Money.zero()
    k2 = _ok(read(KPIS["K2"], **default))
    [course] = [
        r for r in reports["accrual"]["class_rows"] if r["class_type"] == "course"
    ]
    assert (
        k2["total"]["revenue"],
        k2["total"]["direct_tutor_cost"],
        k2["total"]["gross_profit"],
    ) == (course["revenue"], course["direct_tutor_cost"], course["gross_profit"])
    assert Money(k2["total"]["revenue"]) > Money.zero()
    rooms = _ok(read(KPIS["K4"], **default))["rooms"]
    assert {r["room"]["name"] for r in rooms} >= {
        "Aula 1",
        "Aula 2",
        "Sala de usos múltiples",
    }
    assert all(r["utilization_percent"] is not None for r in rooms)
    k5 = _ok(read(KPIS["K5"], **default))
    assert k5["active_current"] >= 4
    assert k5["new"] >= 1
    tutors = {
        t["tutor"]["display_name"]: t
        for t in _ok(read(KPIS["K6"], **default))["tutors"]
    }
    assert {"Demo Tutor", "Demo Second Tutor"} <= set(tutors)
    for row in tutors.values():
        assert sum(row["attendance"].values()) > 0

    # D19 (E6, Q1): two weeks have previous activity: students retained and
    # new. No range ending at T has a lost student (every active student
    # also has a class in the last two weeks): reported on the issue, README
    # says so.
    k5 = _ok(read(KPIS["K5"], **_ranged(*TWO_WEEKS)))
    assert (k5["previous_from"], k5["previous_to"]) == (
        (T - timedelta(days=27)).isoformat(),
        (T - timedelta(days=14)).isoformat(),
    )
    assert k5["retained"] >= 1
    assert k5["new"] >= 1
    assert k5["lost"] == 0


def _export(client: TestClient, cookie: Any, username: str, body: Any) -> str:
    """``POST /exports`` (reauthenticating once when asked), then the
    in-process run; returns the job id."""
    response = post(client, cookie, EXPORTS, body)
    if response.status_code == 403 and _code(response) == "reauth_required":
        _reauthenticate(client, cookie, username)
        response = post(client, cookie, EXPORTS, body)
    return str(_ok(response, 202)["id"])


def test_exports_replays_layouts_and_a_restore(migrated_url, env_for, clock):
    # D11-D14, D16, D22, D26 on their own database.
    _seeded(migrated_url, env_for, clock)
    url = migrated_url
    plan = plan_demo_admin(T)
    plan_row = _deleted_plan(url)

    with auth_client(url) as client:
        cookies = _cookies(client)

        def read(who: str, path: str, **params: Any) -> Any:
            return get(client, cookies[who], path, **params)

        # D11: demo.finance's expenses for the plan's range.
        filters = {
            "incurred_from": plan.incurred_from.isoformat(),
            "incurred_to": plan.incurred_to.isoformat(),
        }
        by_hand = {"dataset": "expenses", "format": "csv", "filters": filters}
        finance_job = _export(client, cookies["demo.finance"], "demo.finance", by_hand)
        # D12: demo.admin's active students, XLSX.
        students_job = _export(
            client,
            cookies["demo.admin"],
            "demo.admin",
            {"dataset": "students", "format": "xlsx", "filters": {"status": "active"}},
        )
        # D16: demo.owner replays the shared setup's parameters.
        [shared] = _ok(read("demo.owner", SETUPS))["items"]
        replay_job = _export(
            client, cookies["demo.owner"], "demo.owner", shared["parameters"]
        )
        run_job(url)

        finance_export = _ok(read("demo.finance", f"{EXPORTS}/{finance_job}"))
        expenses = _ok(read("demo.finance", "/api/v1/finance/expenses", **filters))
        assert finance_export["status"] == "ready"
        assert finance_export["row_count"] == expenses["total"]
        students_export = _ok(read("demo.admin", f"{EXPORTS}/{students_job}"))
        active = _ok(read("demo.admin", "/api/v1/students", status="active"))
        assert students_export["status"] == "ready"
        assert students_export["row_count"] == active["total"]
        replayed = _ok(read("demo.owner", f"{EXPORTS}/{replay_job}"))
        assert replayed["status"] == "ready"
        assert replayed["row_count"] == finance_export["row_count"]

        # D13: who may not export expenses or list datasets; another's job.
        for who in ("demo.coordinator", "demo.admin"):
            refused = post(client, cookies[who], EXPORTS, by_hand)
            assert _status(refused) == 403, (who, refused.text)
        for who in FAMILY_AND_TUTOR:
            assert _status(read(who, f"{EXPORTS}/datasets")) == 403, who
        for suffix in ("", "/download"):
            path = f"{EXPORTS}/{finance_job}{suffix}"
            assert _status(read("demo.owner", path)) == 404, suffix

        # D22: demo.finance reorders and hides, then resets.
        order = [*reversed(WIDGETS["demo.finance"])]
        saved = _ok(
            _put(
                client,
                cookies["demo.finance"],
                LAYOUT,
                {"widgets": [{"widget": w, "visible": w != "balances"} for w in order]},
            )
        )
        assert saved["customized"] is True
        again = _ok(read("demo.finance", LAYOUT))
        assert [(w["widget"], w["visible"]) for w in again["widgets"]] == [
            (w, w != "balances") for w in order
        ]
        _ok(_delete(client, cookies["demo.finance"], LAYOUT), 204)
        reset = _ok(read("demo.finance", LAYOUT))
        assert reset["customized"] is False
        assert [w["widget"] for w in reset["widgets"]] == list(WIDGETS["demo.finance"])

        # D26: demo.tutor restores the draft; it is a live draft again.
        restored = post(
            client,
            cookies["demo.tutor"],
            f"{DELETED}/learning_plan/{plan_row.id}/restore",
            {"version": plan_row.version},
        )
        _ok(restored)
        live = _ok(read("demo.tutor", f"/api/v1/learning-plans/{plan_row.id}"))
        assert live["status"] == "draft"
        assert _ok(read("demo.tutor", DELETED))["items"] == []


async def _retention(url: str) -> int:
    engine = short_lived_engine(url)
    try:
        result = await run_retention_with(
            create_sessionmaker(engine), now=FROZEN_NOW + timedelta(days=31)
        )
        return result.total
    finally:
        await engine.dispose()


def test_the_retention_run_deletes_only_the_draft(migrated_url, env_for, clock):
    # D28: 31 days after the delete, the retention run hard-deletes the draft
    # and nothing else; every protected table keeps its rows (audit_events
    # only grows).
    _seeded(migrated_url, env_for, clock)
    url = migrated_url
    protected = sorted(protected_tables())
    before = {table: count(url, table) for table in protected}
    plans = [
        count(url, t)
        for t in (
            "learning_plans",
            "learning_plan_objectives",
            "learning_plan_milestones",
        )
    ]

    assert asyncio.run(_retention(url)) >= 1

    after = {table: count(url, table) for table in protected}
    assert after["audit_events"] > before["audit_events"]
    assert {t: n for t, n in after.items() if t != "audit_events"} == {
        t: n for t, n in before.items() if t != "audit_events"
    }
    assert [
        count(url, t)
        for t in (
            "learning_plans",
            "learning_plan_objectives",
            "learning_plan_milestones",
        )
    ] == [n - 1 for n in plans]
    assert (
        run_sql(
            url,
            "SELECT id FROM learning_plans WHERE title = :t",
            {"t": DELETED_PLAN_TITLE},
        )
        == []
    )
