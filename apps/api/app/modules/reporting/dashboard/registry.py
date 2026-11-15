"""The dashboard widget registry and the effective layout (#127 B1, B3; E3,
E6).

A widget is one #126 KPI section (M8-09b). It is **available** to a user
exactly when they pass its KPI route's permission check, so its requirement
is built from the constants those routes use (a unit test pins each one
against its route). The registry order is the default order (E3): K1, K3,
K2, K4, K5, K6. The layout never holds figures: widget data comes only from
the KPI routes, which check the caller on every request.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal, get_args

from app.core.authz import Principal
from app.modules.reporting.kpis_router import SCHEDULE_VIEW, TUTOR_VIEW
from app.modules.reporting.router import REPORT_VIEW

type WidgetCode = Literal[
    "revenue_trend",
    "balances",
    "course_profitability",
    "room_utilization",
    "student_retention",
    "tutor_performance",
]
WIDGET_CODES: Final[tuple[str, ...]] = get_args(WidgetCode.__value__)


@dataclass(frozen=True)
class Widget:
    """One widget: its code, the permissions its KPI route checks (all of
    them are needed) and that route's path (the B2 pin)."""

    code: WidgetCode
    permissions: frozenset[str]
    route: str


WIDGETS: Final[tuple[Widget, ...]] = (
    Widget(
        "revenue_trend",
        frozenset({REPORT_VIEW}),
        "/api/v1/reports/kpis/revenue-trend",
    ),
    Widget(
        "balances",
        frozenset({REPORT_VIEW}),
        "/api/v1/reports/finance/balances",
    ),
    Widget(
        "course_profitability",
        frozenset({REPORT_VIEW}),
        "/api/v1/reports/kpis/course-profitability",
    ),
    Widget(
        "room_utilization",
        frozenset({SCHEDULE_VIEW}),
        "/api/v1/reports/kpis/room-utilization",
    ),
    Widget(
        "student_retention",
        frozenset({SCHEDULE_VIEW}),
        "/api/v1/reports/kpis/student-retention",
    ),
    Widget(
        "tutor_performance",
        frozenset({SCHEDULE_VIEW, TUTOR_VIEW}),
        "/api/v1/reports/kpis/tutor-performance",
    ),
)


@dataclass(frozen=True)
class LayoutEntry:
    widget: WidgetCode
    visible: bool


def available_widgets(principal: Principal) -> tuple[WidgetCode, ...]:
    """The widgets whose every permission ``principal`` holds, in registry
    order (the role default, E3; a user with two roles gets the union)."""
    return tuple(
        widget.code
        for widget in WIDGETS
        if all(principal.has(permission) for permission in widget.permissions)
    )


def effective_layout(
    principal: Principal, stored: Iterable[Mapping[str, Any]] | None
) -> list[LayoutEntry]:
    """The layout ``principal`` sees (B3), computed on every read:

    1. the available widgets, in registry order;
    2. the stored entries whose widget is available, in stored order, first
       occurrence only (unknown codes, unavailable widgets and malformed
       entries are dropped);
    3. every available widget still missing, appended with ``visible: true``.

    The result lists exactly the available widgets, each once. With nothing
    stored it is the role default.
    """
    available = available_widgets(principal)
    kept: list[LayoutEntry] = []
    seen: set[str] = set()
    for entry in stored or ():
        if not isinstance(entry, Mapping):
            continue
        code = entry.get("widget")
        visible = entry.get("visible")
        if code not in available or code in seen or not isinstance(visible, bool):
            continue
        seen.add(code)
        kept.append(LayoutEntry(code, visible))
    kept.extend(LayoutEntry(code, True) for code in available if code not in seen)
    return kept
