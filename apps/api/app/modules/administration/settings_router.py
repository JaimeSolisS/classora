"""Business settings routes (#37; ``app.modules.administration.settings``).

- ``GET /api/v1/business-settings``: every signed-in user
  (``authenticated()``); nothing in it is personal data.
- ``PATCH /api/v1/business-settings``: ``require("settings.manage")``
  (Owners and Administrators), with
  ``recent_auth_guard("settings.payment_methods_or_time_zone")``: changing
  the time zone needs a recent reauthentication (ADR-0007 D10).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` →
``permission_denied`` → ``422`` → ``403 reauth_required`` (time-zone change
only) → ``409 version_conflict`` → ``409 tz_change_requires_migration``.
"""

from typing import Annotated

from fastapi import APIRouter

from app.core.authz import AUTH_RESPONSES, Principal, authenticated, require
from app.core.errors import problem_response_doc
from app.core.reauth import REAUTH_RESPONSES, RecentAuthGuard, recent_auth_guard
from app.core.uow import UnitOfWorkDep
from app.modules.administration import service
from app.modules.administration.schemas import (
    BusinessSettingsDetail,
    BusinessSettingsUpdate,
)

router = APIRouter(prefix="/api/v1", tags=["business-settings"])

SETTINGS_MANAGE = "settings.manage"
TIME_ZONE_ACTION = "settings.payment_methods_or_time_zone"

TimeZoneGuard = Annotated[RecentAuthGuard, recent_auth_guard(TIME_ZONE_ACTION)]


@router.get("/business-settings", responses=AUTH_RESPONSES)
async def get_business_settings(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
) -> BusinessSettingsDetail:
    """The center's name, contact details, time zone and weekly operating
    hours. A weekday that is not listed is closed."""
    return await service.get_business_settings(uow.session)


@router.patch(
    "/business-settings",
    responses={
        **REAUTH_RESPONSES,
        409: problem_response_doc(
            "The settings changed since they were loaded (version_conflict);"
            " reload and retry. Or the time zone can't change because"
            " scheduled classes exist (tz_change_requires_migration)."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing version, a blank or too long"
            " name, a malformed email or phone, an unknown time zone, a null"
            " name, time zone or hours, a repeated weekday, an HH:MM time"
            " out of range, a closing time not after the opening time, or an"
            " unknown field (validation_error)."
        ),
    },
)
async def update_business_settings(
    principal: Annotated[Principal, require(SETTINGS_MANAGE)],
    guard: TimeZoneGuard,
    body: BusinessSettingsUpdate,
    uow: UnitOfWorkDep,
) -> BusinessSettingsDetail:
    """Change the sent fields; fields not sent stay unchanged.
    ``operating_hours`` replaces the whole week. Changing ``time_zone``
    needs a password reauthentication in the last 5 minutes (``403
    reauth_required``) and is refused once classes are scheduled (``409
    tz_change_requires_migration``); sending the current zone needs none."""
    return await service.update_business_settings(uow.session, body, guard=guard)
