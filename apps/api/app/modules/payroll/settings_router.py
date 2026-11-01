"""Payroll settings routes (#106 B; ``app.modules.payroll.settings``;
data-model §11.1).

- ``GET /api/v1/payroll/settings``: ``require("payroll.view")`` (Owners and
  Finance Staff).
- ``PATCH /api/v1/payroll/settings``: ``require("payroll.manage")``, all keys
  required. No reauthentication and no ``Idempotency-Key``, and it queues no
  job (#106 E7).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` → ``403
permission_denied`` → ``422`` (the body) → the row ``FOR UPDATE`` → ``409
version_conflict`` → unchanged: nothing written → ``409 payroll_start_locked``
/ ``409 biweekly_anchor_locked`` → the update.
"""

from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.payroll.schemas import PayrollSettingsDetail, PayrollSettingsUpdate
from app.modules.payroll.settings import get_payroll_settings, update_payroll_settings

PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_MANAGE: Final = "payroll.manage"

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])


@router.get("/settings", responses=PERMISSION_RESPONSES)
async def read_payroll_settings(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    uow: UnitOfWorkDep,
) -> PayrollSettingsDetail:
    """The payroll start date (`null`: payroll is off), the biweekly anchor,
    the salary proration basis, whether the first two are locked because
    periods exist, and the settings' version."""
    return await get_payroll_settings(uow.session)


@router.patch(
    "/settings",
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "The settings changed since they were loaded (version_conflict);"
            " or periods exist, so the start date can't change"
            " (payroll_start_locked); or biweekly periods exist, so the anchor"
            " can't change (biweekly_anchor_locked)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, a malformed date or one outside 2000-2099"
            " (date_out_of_range), an anchor that isn't a Monday"
            " (anchor_not_monday), or an unknown proration basis."
        ),
    },
)
async def change_payroll_settings(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    body: PayrollSettingsUpdate,
    uow: UnitOfWorkDep,
) -> PayrollSettingsDetail:
    """Set the payroll start date (any date, past included; `null` keeps
    payroll off), the biweekly anchor and the salary proration basis. Sending
    the current values changes nothing (the version is still checked).
    Periods open at the next daily run, not now. Once periods exist the start
    date is locked, and once biweekly periods exist the anchor is."""
    return await update_payroll_settings(uow.session, body)
