"""KPI routes: access, the check order, the range rules and the closed
shapes (#126 A1-A3, B6 range errors). Every route is also in
``tests/authz/matrix.toml``; the figures are in
``test_reporting_kpis_finance.py`` and ``test_reporting_kpis_operations.py``.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from tests.conftest import FrozenClock, frozen_clock
from tests.integration import kpi_support as ks
from tests.integration.auth_support import auth_client, signed_headers
from tests.integration.corrections_support import Staff, staff

BASE = "/api/v1/reports/kpis"
REVENUE = f"{BASE}/revenue-trend"
COURSES = f"{BASE}/course-profitability"
ROOMS = f"{BASE}/room-utilization"
RETENTION = f"{BASE}/student-retention"
TUTORS = f"{BASE}/tutor-performance"
ROUTES = (REVENUE, COURSES, ROOMS, RETENTION, TUTORS)
NOW = ks.B5_NOW
"""Noon of 2031-04-01 in Mexico City: the center's today."""
MARCH = {"from": "2031-03-01", "to": "2031-03-31"}


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
def people(module_seeded_url: str, client: TestClient) -> dict[str, Staff]:
    url = module_seeded_url
    made = {
        role: staff(url, client, (role,))
        for role in (OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF)
    }
    made["administrator+payroll.finalize"] = staff(
        url, client, (ADMINISTRATOR,), delegations=["payroll.finalize"]
    )
    for role in (TUTOR, STUDENT, GUARDIAN):
        made[role] = staff(url, client, (role,), reauth=False)
    return made


@pytest.fixture(scope="module")
def data(module_seeded_url: str, frozen: FrozenClock) -> None:
    """Rows for every KPI, so the shapes are checked with content."""
    ks.build_b3(module_seeded_url)
    ks.build_b5(module_seeded_url)
    ks.build_b2(module_seeded_url)
    frozen.set(NOW)


def params_for(path: str, **extra: str) -> dict[str, str]:
    found = dict(MARCH)
    if path == REVENUE:
        found["basis"] = "accrual"
    found.update(extra)
    return found


def call(client: TestClient, who: Staff | None, path: str, params: dict) -> Any:
    client.cookies.clear()
    headers = {} if who is None else signed_headers(who.cookie)
    return client.get(path, params=params, headers=headers)


def problem(response: Any) -> tuple[int, str, list[tuple[Any, ...]]]:
    body = response.json()
    return (
        response.status_code,
        body["code"],
        [(tuple(e["path"]), e["code"]) for e in body.get("errors") or []],
    )


ALLOWED = {
    REVENUE: {OWNER, FINANCE_STAFF},
    COURSES: {OWNER, FINANCE_STAFF},
    ROOMS: {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF},
    RETENTION: {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR, FINANCE_STAFF},
    TUTORS: {OWNER, ADMINISTRATOR, ACADEMIC_COORDINATOR},
}


def test_a2_role_matrix(client, people):
    seen: dict[tuple[str, str], int] = {}
    for path in ROUTES:
        for name, who in people.items():
            response = call(client, who, path, params_for(path))
            seen[(path, name)] = response.status_code
            role = name.split("+")[0]
            allowed = role in ALLOWED[path] and not (
                "+" in name and path in (REVENUE, COURSES)
            )
            if allowed:
                assert response.status_code == 200, (path, name, response.text)
            else:
                assert problem(response)[:2] == (403, "permission_denied"), (
                    path,
                    name,
                )
        assert call(client, None, path, params_for(path)).status_code == 401
    # The delegated Administrator: 403 on the money KPIs, 200 elsewhere.
    delegated = "administrator+payroll.finalize"
    assert [seen[(p, delegated)] for p in ROUTES] == [403, 403, 200, 200, 200]


def test_a1_403_comes_before_422(client, people):
    # Finance Staff lack tutor.view: 403 on tutor-performance even with a
    # malformed query; a Coordinator gets 403 on the money KPIs the same way.
    finance = people[FINANCE_STAFF]
    bad = {"from": "2031-02-30", "to": "nope"}
    assert problem(call(client, finance, TUTORS, bad))[:2] == (
        403,
        "permission_denied",
    )
    coordinator = people[ACADEMIC_COORDINATOR]
    for path in (REVENUE, COURSES):
        assert problem(call(client, coordinator, path, {}))[:2] == (
            403,
            "permission_denied",
        )
    tutor = people[TUTOR]
    for path in ROUTES:
        assert problem(call(client, tutor, path, {}))[:2] == (403, "permission_denied")
    assert call(client, None, TUTORS, bad).status_code == 401


def test_a1_parameters_are_required_and_typed(client, people):
    owner = people[OWNER]
    for path in ROUTES:
        assert call(client, owner, path, params_for(path)).status_code == 200
        no_from = {k: v for k, v in params_for(path).items() if k != "from"}
        assert problem(call(client, owner, path, no_from))[:2] == (
            422,
            "validation_error",
        )
        bad_date = params_for(path, **{"from": "2031-02-30"})
        assert problem(call(client, owner, path, bad_date))[:2] == (
            422,
            "validation_error",
        )
    no_basis = {k: v for k, v in params_for(REVENUE).items() if k != "basis"}
    assert problem(call(client, owner, REVENUE, no_basis))[:2] == (
        422,
        "validation_error",
    )
    bad_basis = params_for(REVENUE, basis="forecast")
    assert problem(call(client, owner, REVENUE, bad_basis))[:2] == (
        422,
        "validation_error",
    )
    assert call(
        client, owner, REVENUE, params_for(REVENUE, basis="cash")
    ).status_code == (200)


INVALID = "report_range_invalid"


def test_b6_range_rules_are_117s(client, people):
    owner = people[OWNER]
    for path in ROUTES:
        same_day = params_for(path, **{"from": "2031-03-15", "to": "2031-03-15"})
        assert call(client, owner, path, same_day).status_code == 200
        reversed_ = params_for(path, **{"from": "2031-03-16", "to": "2031-03-15"})
        assert problem(call(client, owner, path, reversed_)) == (
            422,
            INVALID,
            [(("query", "to"), INVALID)],
        )
        # Today is 2031-04-01 (the center's): to = today works, tomorrow not.
        today = params_for(path, to="2031-04-01")
        assert call(client, owner, path, today).status_code == 200
        tomorrow = params_for(path, to="2031-04-02")
        assert problem(call(client, owner, path, tomorrow)) == (
            422,
            INVALID,
            [(("query", "to"), INVALID)],
        )
        year = params_for(path, **{"from": "2030-04-01", "to": "2031-04-01"})
        assert call(client, owner, path, year).status_code == 200
        too_long = params_for(path, **{"from": "2030-03-31", "to": "2031-04-01"})
        assert problem(call(client, owner, path, too_long)) == (
            422,
            INVALID,
            [(("query", "from"), INVALID)],
        )


def test_b6_today_is_the_centers_not_utc(module_seeded_url, client, frozen):
    # 05:30 UTC on Apr 2 is still Apr 1 in Mexico City (signed in then).
    frozen.set(ks.local("2031-04-01", 23, 30))
    owner = staff(module_seeded_url, client, (OWNER,))
    for path in ROUTES:
        assert (
            call(client, owner, path, params_for(path, to="2031-04-01")).status_code
            == 200
        )
        assert problem(call(client, owner, path, params_for(path, to="2031-04-02")))[
            :2
        ] == (422, INVALID)


# --- A3 shapes --------------------------------------------------------------------

FORBIDDEN_KEYS = {
    "description",
    "receipt_reference",
    "note",
    "notes",
    "reason",
    "reference",
    "family",
    "family_id",
    "guardian",
    "guardian_id",
    "student",
    "student_id",
    "student_name",
    "payer_name",
}
MONEY_WORDS = ("revenue", "cost", "profit", "expense", "amount", "net_", "balance")


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


@pytest.mark.usefixtures("data")
def test_a3_closed_shapes_and_no_text_or_people(client, people):
    owner = people[OWNER]
    bodies = {
        path: call(client, owner, path, params_for(path)).json() for path in ROUTES
    }
    cash = call(client, owner, REVENUE, params_for(REVENUE, basis="cash")).json()
    for path, body in [*bodies.items(), ("cash", cash)]:
        assert not _keys(body) & FORBIDDEN_KEYS, path
    for path in (ROOMS, RETENTION, TUTORS):
        money = [k for k in _keys(bodies[path]) if any(w in k for w in MONEY_WORDS)]
        assert money == [], path

    trend = bodies[REVENUE]
    assert set(trend) == {"basis", "from", "to", "today", "months", "totals"}
    assert set(trend["months"][0]) == {
        "month",
        "from",
        "to",
        "revenue_total",
        "direct_tutor_cost_total",
        "gross_profit",
        "operating_expenses_total",
        "net_result",
    }
    assert cash["basis"] == "cash"

    courses = bodies[COURSES]
    assert set(courses) == {"from", "to", "today", "cohorts", "not_attributed", "total"}
    assert courses["cohorts"], "B2 gives March cohorts"
    assert set(courses["cohorts"][0]) == {
        "cohort",
        "revenue",
        "direct_tutor_cost",
        "gross_profit",
    }
    assert set(courses["cohorts"][0]["cohort"]) == {"id", "name"}

    rooms = bodies[ROOMS]
    assert set(rooms) == {"from", "to", "rooms", "total"}
    assert rooms["rooms"]
    assert set(rooms["rooms"][0]) == {
        "room",
        "available_minutes",
        "used_minutes",
        "utilization_percent",
    }
    assert set(rooms["rooms"][0]["room"]) == {"id", "name"}

    retention = bodies[RETENTION]
    assert set(retention) == {
        "from",
        "to",
        "previous_from",
        "previous_to",
        "active_previous",
        "active_current",
        "retained",
        "new",
        "lost",
        "retention_percent",
        "months",
    }
    assert set(retention["months"][0]) == {"month", "from", "to", "active_students"}

    tutors = bodies[TUTORS]
    assert set(tutors) == {"from", "to", "tutors", "totals"}
    assert tutors["tutors"]
    row = tutors["tutors"][0]
    assert set(row["tutor"]) == {"id", "display_name"}
    assert set(row) == {
        "tutor",
        "sessions",
        "completed",
        "pending_closeout",
        "cancelled",
        "scheduled_minutes",
        "students",
        "attendance",
        "attendance_percent",
    }
    assert set(row["attendance"]) == {"present", "late", "absent", "not_recorded"}
    assert set(tutors["totals"]) == set(row) - {"tutor"}
