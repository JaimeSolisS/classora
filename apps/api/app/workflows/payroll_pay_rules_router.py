"""Session pay rule routes (#441 D; ``app.workflows.session_pay_rules``;
ADR-0017 E4, E12).

- ``GET /api/v1/payroll/pay-rules``: ``require("payroll.view")``.
- ``POST /api/v1/payroll/pay-rules`` and ``DELETE
  /api/v1/payroll/pay-rules/{rule_id}``: ``require("payroll.manage")``.

Both permissions are held by Owners and Finance Staff; Administrators see no
pay, also with ``payroll.finalize`` delegated. No reauthentication and no
``Idempotency-Key``: a rule is configuration (#105 precedent). Rules are
center-wide, so the own-pay rule (#105 E5) doesn't apply.

Check order of the ``POST``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` (body) → the advisory lock → ``409 version_conflict`` → ``422
invalid_effective_from`` → ``201``. Of the ``DELETE``: ``401`` →
``csrf_failed`` → ``permission_denied`` → ``404`` → ``409 pay_rule_in_effect``
→ ``204``. The audit events are the automatic insert and delete events with
the staff actor; ``percent`` and ``amount`` are masked in them.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.payroll.schemas import (
    SessionPayRuleCreate,
    SessionPayRuleItem,
    SessionPayRuleOverview,
)
from app.workflows import session_pay_rules

PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_MANAGE: Final = "payroll.manage"

router = APIRouter(prefix="/api/v1/payroll/pay-rules", tags=["payroll"])


@router.get("", responses=PERMISSION_RESPONSES)
async def get_pay_rules(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    uow: UnitOfWorkDep,
) -> SessionPayRuleOverview:
    """For each event, the rule in force on the center's today, the upcoming
    versions (oldest first) and the past ones (newest first, at most 50)."""
    return await session_pay_rules.get_overview(uow.session)


@router.post(
    "",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "based_on_id is not the event's latest version (version_conflict)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, an unknown event or mode, the percent (rate_invalid,"
            " rate_precision, rate_out_of_range, percent_out_of_range), the"
            " amount (money_invalid, money_precision, money_out_of_range,"
            " amount_negative), or a value that doesn't match the mode"
            " (pay_rule_values_mismatch). Or effective_from is in the past or"
            " not later than every version of the event"
            " (invalid_effective_from)."
        ),
    },
)
async def create_pay_rule(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    body: SessionPayRuleCreate,
    uow: UnitOfWorkDep,
) -> SessionPayRuleItem:
    """Publish a new version of an event's rule, from the center's today or
    later. Versions are never edited: to change an upcoming one, delete it
    and create another. Past work is never repriced."""
    return await session_pay_rules.create_rule(uow.session, body)


@router.delete(
    "/{rule_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such rule version (not_found)."),
        409: problem_response_doc(
            "The version is in force or in the past; only an upcoming version"
            " can be deleted (pay_rule_in_effect)."
        ),
    },
)
async def delete_pay_rule(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    rule_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming rule version."""
    await session_pay_rules.delete_rule(uow.session, rule_id)
