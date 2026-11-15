"""The dashboard layout of the caller (#127 C2-C5; E5).

Each user reads and writes only their own row; there is no ``user_id``
parameter, the Owner included. The effective layout is computed again on
every read (:func:`~.registry.effective_layout`), and a read never writes,
even when the stored row holds entries that are no longer available. Every
function runs in the caller's Unit of Work and never commits.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import PermissionDeniedError, Principal
from app.modules.reporting.dashboard.errors import DashboardWidgetUnavailableError
from app.modules.reporting.dashboard.models import DashboardLayout
from app.modules.reporting.dashboard.registry import (
    LayoutEntry,
    available_widgets,
    effective_layout,
)
from app.modules.reporting.dashboard.schemas import (
    DashboardLayoutResponse,
    DashboardLayoutUpdate,
    DashboardWidget,
)


def ensure_has_dashboard(principal: Principal) -> None:
    """``403 permission_denied`` unless some widget is available (tutors,
    students, guardians and any account without a KPI permission)."""
    if not available_widgets(principal):
        raise PermissionDeniedError()


def _response(
    entries: list[LayoutEntry], *, customized: bool
) -> DashboardLayoutResponse:
    return DashboardLayoutResponse(
        widgets=[
            DashboardWidget(widget=entry.widget, visible=entry.visible)
            for entry in entries
        ],
        customized=customized,
    )


async def get_layout(
    session: AsyncSession, *, principal: Principal
) -> DashboardLayoutResponse:
    """The caller's effective layout: one statement (their row), no write."""
    stored = (
        await session.execute(
            select(DashboardLayout.widgets).where(
                DashboardLayout.user_id == principal.user_id
            )
        )
    ).scalar_one_or_none()
    return _response(effective_layout(principal, stored), customized=stored is not None)


async def save_layout(
    session: AsyncSession, *, principal: Principal, body: DashboardLayoutUpdate
) -> DashboardLayoutResponse:
    """Store the body as sent (an upsert on ``user_id``), after ``422
    dashboard_widget_unavailable`` for the first widget the caller may not
    have. Returns the effective layout of what was stored."""
    available = set(available_widgets(principal))
    for index, entry in enumerate(body.widgets):
        if entry.widget not in available:
            raise DashboardWidgetUnavailableError(index)
    widgets = [entry.model_dump(mode="json") for entry in body.widgets]
    await session.execute(
        insert(DashboardLayout)
        .values(user_id=principal.user_id, widgets=widgets)
        .on_conflict_do_update(
            index_elements=[DashboardLayout.user_id],
            set_={"widgets": widgets, "updated_at": func.now()},
        )
    )
    return _response(effective_layout(principal, widgets), customized=True)


async def reset_layout(session: AsyncSession, *, principal: Principal) -> None:
    """Delete the caller's row (back to the role default); none is fine."""
    await session.execute(
        delete(DashboardLayout).where(DashboardLayout.user_id == principal.user_id)
    )
