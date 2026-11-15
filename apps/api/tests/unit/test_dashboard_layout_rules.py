"""The dashboard widget registry, the effective layout and the layout body
(#127 B1-B3, C3)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.authz import (
    AccessRule,
    Principal,
    get_current_principal,
    route_access_rules,
)
from app.main import build_app
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    ROLE_PERMISSIONS,
    STUDENT,
    TUTOR,
)
from app.modules.reporting.dashboard.registry import (
    WIDGET_CODES,
    WIDGETS,
    LayoutEntry,
    available_widgets,
    effective_layout,
)
from app.modules.reporting.dashboard.schemas import DashboardLayoutUpdate

ALL = list(WIDGET_CODES)
FINANCE = ["revenue_trend", "balances", "course_profitability"]
OPERATIONS = ["room_utilization", "student_retention", "tutor_performance"]


def principal(*permissions: str) -> Principal:
    return Principal(
        user_id=uuid.uuid7(), roles=frozenset(), permissions=frozenset(permissions)
    )


def role(*codes: str) -> Principal:
    permissions: set[str] = set()
    for code in codes:
        permissions |= set(ROLE_PERMISSIONS[code])
    return Principal(
        user_id=uuid.uuid7(),
        roles=frozenset(codes),
        permissions=frozenset(permissions),
    )


def entries(*pairs: tuple[str, bool]) -> list[LayoutEntry]:
    return [LayoutEntry(code, visible) for code, visible in pairs]  # type: ignore[arg-type]


def codes(layout: list[LayoutEntry]) -> list[str]:
    return [entry.widget for entry in layout]


# --- B1: the registry ------------------------------------------------------------


def test_the_registry_is_the_six_sections_in_the_default_order():
    assert [widget.code for widget in WIDGETS] == [
        "revenue_trend",
        "balances",
        "course_profitability",
        "room_utilization",
        "student_retention",
        "tutor_performance",
    ]
    assert list(WIDGET_CODES) == [widget.code for widget in WIDGETS]
    assert {widget.code: set(widget.permissions) for widget in WIDGETS} == {
        "revenue_trend": {"finance.report.view"},
        "balances": {"finance.report.view"},
        "course_profitability": {"finance.report.view"},
        "room_utilization": {"schedule.view"},
        "student_retention": {"schedule.view"},
        "tutor_performance": {"schedule.view", "tutor.view"},
    }


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        ((OWNER,), ALL),
        ((FINANCE_STAFF,), [*FINANCE, "room_utilization", "student_retention"]),
        ((ADMINISTRATOR,), OPERATIONS),
        ((ACADEMIC_COORDINATOR,), OPERATIONS),
        ((ACADEMIC_COORDINATOR, FINANCE_STAFF), ALL),
        ((TUTOR,), []),
        ((STUDENT,), []),
        ((GUARDIAN,), []),
    ],
)
def test_the_role_defaults(roles, expected):
    who = role(*roles)
    assert list(available_widgets(who)) == expected
    assert effective_layout(who, None) == [LayoutEntry(c, True) for c in expected]


# --- B2: each widget's requirement is its KPI route's -----------------------------


@pytest.fixture(scope="module")
def app() -> FastAPI:
    return build_app()


def _route(app: FastAPI, path: str) -> RouteContext:
    [found] = [
        r
        for r in iter_route_contexts(app.routes)
        if isinstance(r.original_route, APIRoute)
        and str(r.path) == path
        and "GET" in r.original_route.methods
    ]
    return found


@pytest.mark.parametrize("widget", WIDGETS, ids=lambda widget: widget.code)
def test_the_route_access_rule_is_one_of_the_widget_permissions(app, widget):
    [rule] = route_access_rules(_route(app, widget.route))
    assert rule.kind == "permission"
    assert rule == AccessRule("permission", permission=rule.permission)
    assert rule.permission in widget.permissions


@pytest.mark.parametrize(
    ("widget", "missing"),
    [(w, p) for w in WIDGETS for p in sorted(w.permissions)],
    ids=lambda value: getattr(value, "code", value),
)
def test_the_route_refuses_a_caller_missing_any_widget_permission(app, widget, missing):
    """Every permission of the widget is checked by its route (K6's
    ``tutor.view`` in its own dependency), before the query is read."""
    everything = {p for permissions in ROLE_PERMISSIONS.values() for p in permissions}
    who = principal(*(everything - {missing}))
    app.dependency_overrides[get_current_principal] = lambda: who
    try:
        response = TestClient(app, raise_server_exceptions=False).get(widget.route)
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "permission_denied"


# --- B3: the effective layout -----------------------------------------------------


def test_the_stored_order_and_visibility_are_kept():
    stored = [
        {"widget": "tutor_performance", "visible": False},
        {"widget": "room_utilization", "visible": True},
        {"widget": "student_retention", "visible": False},
    ]
    assert effective_layout(role(ADMINISTRATOR), stored) == entries(
        ("tutor_performance", False),
        ("room_utilization", True),
        ("student_retention", False),
    )


def test_missing_available_widgets_are_appended_shown():
    stored = [{"widget": "student_retention", "visible": False}]
    assert effective_layout(role(ADMINISTRATOR), stored) == entries(
        ("student_retention", False),
        ("room_utilization", True),
        ("tutor_performance", True),
    )


def test_unknown_unavailable_duplicate_and_malformed_entries_are_dropped():
    stored: list[Any] = [
        {"widget": "revenue_trend", "visible": False},  # unavailable
        {"widget": "gone_widget", "visible": True},  # unknown
        {"widget": "tutor_performance", "visible": False},
        {"widget": "tutor_performance", "visible": True},  # duplicate
        {"widget": "room_utilization", "visible": "yes"},  # not a boolean
        {"widget": "student_retention"},  # no visible
        "room_utilization",  # not an object
    ]
    assert effective_layout(role(ACADEMIC_COORDINATOR), stored) == entries(
        ("tutor_performance", False),
        ("room_utilization", True),
        ("student_retention", True),
    )


def test_a_lost_permission_drops_and_a_regained_one_appends():
    stored = [
        {"widget": "balances", "visible": False},
        {"widget": "room_utilization", "visible": False},
        {"widget": "revenue_trend", "visible": True},
    ]
    assert codes(effective_layout(role(ADMINISTRATOR), stored)) == OPERATIONS[:1] + [
        "student_retention",
        "tutor_performance",
    ]
    assert effective_layout(role(OWNER), stored) == entries(
        ("balances", False),
        ("room_utilization", False),
        ("revenue_trend", True),
        ("course_profitability", True),
        ("student_retention", True),
        ("tutor_performance", True),
    )


def test_a_two_role_union_keeps_the_stored_order():
    stored = [{"widget": code, "visible": True} for code in reversed(ALL)]
    who = role(ACADEMIC_COORDINATOR, FINANCE_STAFF)
    assert codes(effective_layout(who, stored)) == list(reversed(ALL))


def test_no_available_widget_is_an_empty_layout():
    stored = [{"widget": "room_utilization", "visible": True}]
    assert effective_layout(role(TUTOR), stored) == []


# --- C3: the body -----------------------------------------------------------------


def body(*widgets: Any) -> DashboardLayoutUpdate:
    return DashboardLayoutUpdate.model_validate({"widgets": list(widgets)})


def test_a_valid_body():
    parsed = body(
        {"widget": "room_utilization", "visible": False},
        {"widget": "tutor_performance", "visible": True},
    )
    assert [(w.widget, w.visible) for w in parsed.widgets] == [
        ("room_utilization", False),
        ("tutor_performance", True),
    ]


def _locations(error: ValidationError) -> list[tuple[Any, ...]]:
    return [tuple(detail["loc"]) for detail in error.errors()]


@pytest.mark.parametrize(
    ("value", "location"),
    [
        ({"widgets": []}, ("widgets",)),
        ({"widgets": [{"widget": "room_utilization", "visible": True}] * 21}, None),
        ({}, ("widgets",)),
        ({"widgets": [{"widget": "nope", "visible": True}]}, ("widgets", 0, "widget")),
        ({"widgets": [{"widget": "balances"}]}, ("widgets", 0, "visible")),
        (
            {"widgets": [{"widget": "balances", "visible": True, "size": 2}]},
            ("widgets", 0, "size"),
        ),
        (
            {"widgets": [{"widget": "balances", "visible": "maybe"}]},
            ("widgets", 0, "visible"),
        ),
        ({"widgets": [], "extra": 1}, None),
    ],
)
def test_invalid_bodies(value, location):
    with pytest.raises(ValidationError) as caught:
        DashboardLayoutUpdate.model_validate(value)
    if location is not None:
        assert location in _locations(caught.value)


def test_a_duplicate_widget_is_refused_at_the_repeated_item():
    with pytest.raises(ValidationError) as caught:
        body(
            {"widget": "balances", "visible": True},
            {"widget": "revenue_trend", "visible": True},
            {"widget": "balances", "visible": False},
        )
    [detail] = caught.value.errors()
    assert detail["loc"] == ("widgets", 2, "widget")
    assert detail["type"] == "widget_duplicate"
