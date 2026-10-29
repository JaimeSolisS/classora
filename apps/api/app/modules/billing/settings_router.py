"""Billing settings routes (#91 B2-B4; ``app.modules.billing.settings``;
data-model §3.2).

- ``GET /api/v1/billing/settings``: ``require("billing.view")`` (Owners,
  Administrators and Finance Staff).
- ``PATCH /api/v1/billing/settings``: ``require("billing.manage")``, body
  ``{version, billing_starts_on}`` (``null`` turns billing off). No
  reauthentication and no ``Idempotency-Key``: the setting posts nothing by
  itself (ADR-0007's reauthentication list is unchanged).

Check order of the ``PATCH``: ``401`` → ``csrf_failed`` → ``403
permission_denied`` → ``422`` (the body) → the row ``FOR UPDATE`` → ``409
version_conflict``. After the commit, a ``PATCH`` whose result is a date
queues one ``billing:post_session_charges`` run
(``app.core.jobs.defer_session_charges``) and one
``billing:post_enrollment_charges`` run (``defer_enrollment_charges``,
#92 D1) and one ``billing:post_tuition_charges`` run
(``defer_tuition_charges``, #93 E1), so staff see the charges without
waiting for the next tick; a failed ``PATCH`` queues nothing.
"""

from typing import Annotated, Final

from fastapi import APIRouter, BackgroundTasks, Depends
from procrastinate import App

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.jobs import (
    defer_enrollment_charges,
    defer_session_charges,
    defer_tuition_charges,
    get_job_app,
)
from app.core.uow import UnitOfWorkDep
from app.modules.billing.schemas import BillingSettingsDetail, BillingSettingsUpdate
from app.modules.billing.settings import get_billing_settings, update_billing_settings

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])

JobAppDep = Annotated[App, Depends(get_job_app)]


@router.get("/settings", responses=PERMISSION_RESPONSES)
async def read_billing_settings(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    uow: UnitOfWorkDep,
) -> BillingSettingsDetail:
    """The billing start date (`billing_starts_on`; `null`: billing is off)
    and the settings' version."""
    return await get_billing_settings(uow.session)


@router.patch(
    "/settings",
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "The settings changed since they were loaded (version_conflict);"
            " reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing `version` or"
            " `billing_starts_on`, a malformed date or one outside 2000-2099"
            " (date_out_of_range), or an unknown field."
        ),
    },
)
async def change_billing_settings(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: BillingSettingsUpdate,
    uow: UnitOfWorkDep,
    background: BackgroundTasks,
    job_app: JobAppDep,
) -> BillingSettingsDetail:
    """Set the billing start date (any date, past included) or turn billing
    off with `null`. Charges are posted for confirmed and completed
    individual sessions on or after the date; moving the date later or
    clearing it pauses posting and never changes posted charges. Sending the
    current value changes nothing (the version is still checked). After a
    date is set, the charges are posted by a queued job."""
    updated = await update_billing_settings(uow.session, body)
    if updated.billing_starts_on is not None:
        # Runs after the response, so after the Unit of Work committed.
        background.add_task(defer_session_charges, job_app)
        background.add_task(defer_enrollment_charges, job_app)
        background.add_task(defer_tuition_charges, job_app)
    return updated
