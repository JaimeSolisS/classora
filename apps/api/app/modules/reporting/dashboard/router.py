"""The dashboard layout routes (#127 C; ``_docs/api.md`` Dashboard layout).

All three are ``authenticated()`` plus :func:`dashboard_user`: ``403
permission_denied`` when no widget is available to the caller (tutors,
students and guardians), before the body. Each user reads and writes only
their own layout. Widget data never comes from here: each widget reads its
own KPI route, which checks the caller on every request (#127 E6).

``PUT`` carries no ``Idempotency-Key``: it is idempotent by nature.
"""

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.reporting.dashboard import service
from app.modules.reporting.dashboard.schemas import (
    DashboardLayoutResponse,
    DashboardLayoutUpdate,
)

router = APIRouter(prefix="/api/v1/reports/dashboard", tags=["reports"])

_LAYOUT: Final = "/layout"


async def dashboard_user(
    principal: Annotated[Principal, authenticated()],
) -> Principal:
    """``403`` unless some widget is available to the caller."""
    service.ensure_has_dashboard(principal)
    return principal


DashboardUser = Annotated[Principal, Depends(dashboard_user)]


@router.get(_LAYOUT, responses={**PERMISSION_RESPONSES})
async def get_dashboard_layout(
    principal: DashboardUser, uow: UnitOfWorkDep
) -> DashboardLayoutResponse:
    """Your dashboard layout: exactly the widgets you may see (each one's KPI
    route permission), in your saved order, or the role default (every
    available widget, shown, in the catalog order)."""
    return await service.get_layout(uow.session, principal=principal)


@router.put(
    _LAYOUT,
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "The body is not valid: the shape, an unknown widget or a widget"
            " listed twice (validation_error); a widget not available to the"
            " caller (dashboard_widget_unavailable)."
        ),
    },
)
async def put_dashboard_layout(
    principal: DashboardUser, body: DashboardLayoutUpdate, uow: UnitOfWorkDep
) -> DashboardLayoutResponse:
    """Save your dashboard layout: the widgets in order, each shown or
    hidden. Available widgets you leave out come back, shown, at the end."""
    return await service.save_layout(uow.session, principal=principal, body=body)


@router.delete(
    _LAYOUT,
    status_code=204,
    response_class=Response,
    responses={**PERMISSION_RESPONSES},
)
async def delete_dashboard_layout(
    principal: DashboardUser, uow: UnitOfWorkDep
) -> Response:
    """Reset your dashboard to the role default."""
    await service.reset_layout(uow.session, principal=principal)
    return Response(status_code=204)
