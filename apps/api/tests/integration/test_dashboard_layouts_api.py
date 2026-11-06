"""The dashboard layout routes (#127 C2-C5, F1, F2) over HTTP.

Widget data never comes from these routes: each widget reads its own KPI
route. F1 pins, per seeded role, that the layout lists exactly the widgets
whose KPI route answers ``200`` for that user (and every other catalog
widget's route ``403``).
"""

from collections.abc import Iterator
from typing import Any, Final

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.identity import service as identity
from app.modules.identity.permissions import (
    ACADEMIC_COORDINATOR,
    ADMINISTRATOR,
    FINANCE_STAFF,
    GUARDIAN,
    OWNER,
    STUDENT,
    TUTOR,
)
from app.modules.reporting.dashboard import service
from app.modules.reporting.dashboard.registry import WIDGET_CODES, WIDGETS
from tests.authz.csrf import ORIGIN_HEADER
from tests.integration.auth_support import (
    cookie_header,
    create_user,
    signed_headers,
)
from tests.integration.catalog_support import count_statements
from tests.integration.conftest import run_sql
from tests.integration.export_support import Actor, actor, center_today, set_roles
from tests.integration.invitation_support import sign_in
from tests.integration.lifecycle_support import in_uow

LAYOUT: Final = "/api/v1/reports/dashboard/layout"
ALL: Final = list(WIDGET_CODES)
FINANCE: Final = ["revenue_trend", "balances", "course_profitability"]
OPERATIONS: Final = ["room_utilization", "student_retention", "tutor_performance"]
DEFAULTS: Final = {
    OWNER: ALL,
    FINANCE_STAFF: [*FINANCE, "room_utilization", "student_retention"],
    ADMINISTRATOR: OPERATIONS,
    ACADEMIC_COORDINATOR: OPERATIONS,
}
NO_DASHBOARD: Final = (TUTOR, STUDENT, GUARDIAN)
VALID: Final = {"widgets": [{"widget": "room_utilization", "visible": True}]}


@pytest.fixture(scope="module")
def client(module_seeded_url: str) -> Iterator[TestClient]:
    from tests.integration.auth_support import auth_client

    with auth_client(module_seeded_url) as test_client:
        yield test_client


def code_of(response: Any) -> tuple[int, str | None]:
    if response.status_code < 400:
        return response.status_code, None
    return response.status_code, response.json().get("code")


def get_layout(client: TestClient, who: Actor) -> Any:
    client.cookies.clear()
    return client.get(LAYOUT, headers=signed_headers(who.cookie))


def put_layout(client: TestClient, who: Actor, body: Any) -> Any:
    client.cookies.clear()
    return client.put(LAYOUT, json=body, headers=signed_headers(who.cookie))


def delete_layout(client: TestClient, who: Actor) -> Any:
    client.cookies.clear()
    return client.delete(LAYOUT, headers=signed_headers(who.cookie))


def layout(client: TestClient, who: Actor) -> dict[str, Any]:
    response = get_layout(client, who)
    assert response.status_code == 200, response.text
    result: dict[str, Any] = response.json()
    return result


def saved(client: TestClient, who: Actor, widgets: list[tuple[str, bool]]) -> Any:
    body = {"widgets": [{"widget": w, "visible": v} for w, v in widgets]}
    response = put_layout(client, who, body)
    assert response.status_code == 200, response.text
    return response.json()


def pairs(body: dict[str, Any]) -> list[tuple[str, bool]]:
    return [(item["widget"], item["visible"]) for item in body["widgets"]]


def stored(url: str, who: Actor) -> Any:
    rows = run_sql(
        url,
        "SELECT widgets FROM dashboard_layouts WHERE user_id = :u",
        {"u": who.user.id},
    )
    return rows[0].widgets if rows else None


def kpi_status(client: TestClient, who: Actor, route: str, today: str) -> int:
    params: dict[str, str] = {}
    if "/kpis/" in route:
        params = {"from": today, "to": today}
        if route.endswith("revenue-trend"):
            params["basis"] = "accrual"
    client.cookies.clear()
    return client.get(
        route, params=params, headers=signed_headers(who.cookie)
    ).status_code


# --- C2-C4: access and check order ---------------------------------------------------


def test_anonymous_is_401(client):
    client.cookies.clear()
    assert code_of(client.get(LAYOUT)) == (401, "authentication_required")
    assert code_of(client.put(LAYOUT, json=VALID, headers=ORIGIN_HEADER)) == (
        401,
        "authentication_required",
    )
    assert code_of(client.delete(LAYOUT, headers=ORIGIN_HEADER)) == (
        401,
        "authentication_required",
    )


def test_csrf_comes_before_everything_else(module_seeded_url, client):
    who = actor(module_seeded_url, client, OWNER)
    client.cookies.clear()
    headers = {**ORIGIN_HEADER, **cookie_header(who.cookie)}
    assert code_of(client.put(LAYOUT, json={"bad": 1}, headers=headers)) == (
        403,
        "csrf_failed",
    )
    assert code_of(client.delete(LAYOUT, headers=headers)) == (403, "csrf_failed")
    assert stored(module_seeded_url, who) is None


def test_the_privacy_notice_comes_first(module_seeded_url, client):
    user = create_user(module_seeded_url, roles=(OWNER,), privacy_notice_accepted=False)
    who = Actor(user, sign_in(client, user.username))
    expected = (403, "privacy_notice_required")
    assert code_of(get_layout(client, who)) == expected
    assert code_of(put_layout(client, who, {"bad": 1})) == expected
    assert code_of(delete_layout(client, who)) == expected


@pytest.mark.parametrize("role", NO_DASHBOARD)
def test_roles_without_widgets_get_403_before_the_body(module_seeded_url, client, role):
    who = actor(module_seeded_url, client, role)
    assert code_of(get_layout(client, who)) == (403, "permission_denied")
    assert code_of(put_layout(client, who, {"bad": 1})) == (403, "permission_denied")
    assert code_of(put_layout(client, who, VALID)) == (403, "permission_denied")
    assert code_of(delete_layout(client, who)) == (403, "permission_denied")
    assert stored(module_seeded_url, who) is None


@pytest.mark.parametrize(
    ("body", "path"),
    [
        ({}, ["body", "widgets"]),
        ({"widgets": []}, ["body", "widgets"]),
        ({"widgets": [{"widget": "nope", "visible": True}]}, None),
        ({"widgets": [{"widget": "room_utilization"}]}, None),
        (
            {"widgets": [{"widget": "room_utilization", "visible": True, "w": 2}]},
            ["body", "widgets", 0, "w"],
        ),
        ({**VALID, "user_id": "x"}, ["body", "user_id"]),
        (
            {
                "widgets": [
                    {"widget": "room_utilization", "visible": True},
                    {"widget": "room_utilization", "visible": False},
                ]
            },
            ["body", "widgets", 1, "widget"],
        ),
    ],
)
def test_invalid_bodies_are_422_and_store_nothing(
    module_seeded_url, client, body, path
):
    who = actor(module_seeded_url, client, ADMINISTRATOR)
    response = put_layout(client, who, body)
    assert code_of(response) == (422, "validation_error")
    errors = response.json()["errors"]
    if path is not None:
        assert path in [error["path"] for error in errors]
    assert stored(module_seeded_url, who) is None


def test_a_shape_error_comes_before_an_unavailable_widget(module_seeded_url, client):
    who = actor(module_seeded_url, client, ADMINISTRATOR)
    body = {
        "widgets": [
            {"widget": "revenue_trend", "visible": True},
            {"widget": "nope", "visible": True},
        ]
    }
    assert code_of(put_layout(client, who, body)) == (422, "validation_error")


# --- F2: an unavailable widget -----------------------------------------------------


def test_an_unavailable_widget_is_422_and_nothing_is_stored(module_seeded_url, client):
    who = actor(module_seeded_url, client, ADMINISTRATOR)
    body = {
        "widgets": [
            {"widget": "room_utilization", "visible": True},
            {"widget": "revenue_trend", "visible": True},
        ]
    }
    response = put_layout(client, who, body)
    assert code_of(response) == (422, "dashboard_widget_unavailable")
    assert [(e["path"], e["code"]) for e in response.json()["errors"]] == [
        (["body", "widgets", 1, "widget"], "dashboard_widget_unavailable")
    ]
    assert stored(module_seeded_url, who) is None
    assert layout(client, who)["customized"] is False


# --- C2, C3, C4: round trip, persistence and reset ------------------------------------


def test_a_layout_round_trips_and_resets(module_seeded_url, client):
    url = module_seeded_url
    who = actor(url, client, FINANCE_STAFF)
    sent = [("student_retention", False), ("balances", True), ("revenue_trend", False)]

    result = saved(client, who, sent)

    expected = [*sent, ("course_profitability", True), ("room_utilization", True)]
    assert result == {
        "widgets": [{"widget": w, "visible": v} for w, v in expected],
        "customized": True,
    }
    assert stored(url, who) == [{"widget": w, "visible": v} for w, v in sent]
    again = layout(client, who)
    assert again == result

    # Saving again replaces the row (last write wins, no version).
    saved(client, who, [("room_utilization", False)])
    assert pairs(layout(client, who))[0] == ("room_utilization", False)
    [count] = run_sql(
        url,
        "SELECT count(*) AS n FROM dashboard_layouts WHERE user_id = :u",
        {"u": who.user.id},
    )
    assert count.n == 1

    assert code_of(delete_layout(client, who)) == (204, None)
    assert stored(url, who) is None
    assert layout(client, who) == {
        "widgets": [{"widget": w, "visible": True} for w in DEFAULTS[FINANCE_STAFF]],
        "customized": False,
    }
    # Idempotent: no row is still 204.
    assert code_of(delete_layout(client, who)) == (204, None)


def test_each_user_has_their_own_layout(module_seeded_url, client):
    url = module_seeded_url
    a = actor(url, client, OWNER)
    b = actor(url, client, OWNER)

    saved(client, a, [("tutor_performance", False)])

    assert layout(client, b) == {
        "widgets": [{"widget": w, "visible": True} for w in ALL],
        "customized": False,
    }
    assert code_of(delete_layout(client, b)) == (204, None)
    assert stored(url, a) == [{"widget": "tutor_performance", "visible": False}]


def test_get_takes_one_statement_and_never_writes(module_seeded_url, client):
    url = module_seeded_url
    who = actor(url, client, ACADEMIC_COORDINATOR)
    run_sql(
        url,
        "INSERT INTO dashboard_layouts (user_id, widgets)"
        " VALUES (:u, CAST(:w AS jsonb))",
        {
            "u": who.user.id,
            "w": '[{"widget": "revenue_trend", "visible": false},'
            ' {"widget": "gone", "visible": true},'
            ' {"widget": "tutor_performance", "visible": false}]',
        },
    )

    async def principal(session: AsyncSession) -> Any:
        return await identity.load_principal(session, who.user.id)

    loaded = in_uow(url, principal)
    count, result = count_statements(
        url, lambda s: service.get_layout(s, principal=loaded)
    )

    assert count == 1
    assert [(w.widget, w.visible) for w in result.widgets] == [
        ("tutor_performance", False),
        ("room_utilization", True),
        ("student_retention", True),
    ]
    assert result.customized is True
    assert pairs(layout(client, who)) == [
        ("tutor_performance", False),
        ("room_utilization", True),
        ("student_retention", True),
    ]
    # The stale entries stay stored: a read never writes.
    assert [entry["widget"] for entry in stored(url, who)] == [
        "revenue_trend",
        "gone",
        "tutor_performance",
    ]


# --- F2: losing and regaining a permission -------------------------------------------


def test_a_lost_permission_removes_the_widgets_at_once(module_seeded_url, client):
    url = module_seeded_url
    who = actor(url, client, ACADEMIC_COORDINATOR)
    set_roles(url, who.user, ACADEMIC_COORDINATOR, FINANCE_STAFF)
    sent = [
        ("tutor_performance", True),
        ("balances", False),
        ("room_utilization", True),
        ("revenue_trend", True),
        ("student_retention", False),
        ("course_profitability", True),
    ]
    saved(client, who, sent)

    set_roles(url, who.user, ACADEMIC_COORDINATOR)
    assert pairs(layout(client, who)) == [
        ("tutor_performance", True),
        ("room_utilization", True),
        ("student_retention", False),
    ]
    # No write happened: regaining the role restores the stored places.
    set_roles(url, who.user, ACADEMIC_COORDINATOR, FINANCE_STAFF)
    assert pairs(layout(client, who)) == sent

    # Saved while lacking them, the finance widgets come back appended, shown.
    set_roles(url, who.user, ACADEMIC_COORDINATOR)
    saved(
        client,
        who,
        [
            ("student_retention", False),
            ("room_utilization", True),
            ("tutor_performance", True),
        ],
    )
    set_roles(url, who.user, ACADEMIC_COORDINATOR, FINANCE_STAFF)
    assert pairs(layout(client, who)) == [
        ("student_retention", False),
        ("room_utilization", True),
        ("tutor_performance", True),
        ("revenue_trend", True),
        ("balances", True),
        ("course_profitability", True),
    ]

    # Losing every widget: 403, and the row stays.
    set_roles(url, who.user, TUTOR)
    assert code_of(get_layout(client, who)) == (403, "permission_denied")
    assert [entry["widget"] for entry in stored(url, who)] == [
        "student_retention",
        "room_utilization",
        "tutor_performance",
    ]


# --- F1: the layout and the KPI routes agree, per role -------------------------------


@pytest.mark.parametrize("role", [*DEFAULTS, *NO_DASHBOARD])
def test_the_layout_lists_exactly_the_widgets_whose_route_answers(
    module_seeded_url, client, role
):
    url = module_seeded_url
    who = actor(url, client, role)
    today = center_today(url).isoformat()

    response = get_layout(client, who)

    expected = DEFAULTS.get(role, [])
    if expected:
        assert response.json() == {
            "widgets": [{"widget": w, "visible": True} for w in expected],
            "customized": False,
        }
    else:
        assert code_of(response) == (403, "permission_denied")
    for widget in WIDGETS:
        status = kpi_status(client, who, widget.route, today)
        assert status == (200 if widget.code in expected else 403), (
            widget.code,
            status,
        )
