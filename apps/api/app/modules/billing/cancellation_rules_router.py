"""Cancellation billing rule routes (#99 B3;
``app.modules.billing.cancellation_rules``; data-model §3.22).

- ``GET /api/v1/billing/cancellation-rules``: ``require("billing.view")``
  (Owners, Administrators and Finance Staff).
- ``POST /api/v1/billing/cancellation-rules`` and ``DELETE
  /api/v1/billing/cancellation-rules/{rule_id}``:
  ``require("billing.manage")``. No reauthentication and no
  ``Idempotency-Key``: a rule is configuration, not a ledger row.

Check order of the ``POST``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` (body) → the advisory lock → ``409 version_conflict`` → ``422
invalid_effective_from`` → ``201``. Of the ``DELETE``: ``401`` →
``csrf_failed`` → ``permission_denied`` → ``404`` → ``409
policy_version_in_effect`` → ``204``. The audit events are the automatic
insert and delete events with the staff actor.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.billing.cancellation_rules import (
    create_cancellation_rule,
    delete_cancellation_rule,
    get_cancellation_rule_overview,
)
from app.modules.billing.schemas import (
    CancellationRuleCreate,
    CancellationRuleItem,
    CancellationRuleOverview,
)

BILLING_VIEW: Final = "billing.view"
BILLING_MANAGE: Final = "billing.manage"

router = APIRouter(prefix="/api/v1/billing", tags=["billing"])


@router.get("/cancellation-rules", responses=PERMISSION_RESPONSES)
async def get_cancellation_rules(
    principal: Annotated[Principal, require(BILLING_VIEW)],
    uow: UnitOfWorkDep,
) -> CancellationRuleOverview:
    """The cancellation billing rules in force now, the upcoming versions
    (oldest first) and the past ones (newest first, at most 50). A cancelled
    session that allows a makeup is always credited; otherwise its charge is
    credited or stands by the outcome of its classification in the version
    in force when the cancellation was requested (or made by the center)."""
    return await get_cancellation_rule_overview(uow.session)


@router.post(
    "/cancellation-rules",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "based_on_id is not the latest version: the rules changed since"
            " they were loaded (version_conflict); reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing field, an outcome other than"
            " credit or charge, a naive effective_from or an unknown field"
            " (validation_error); or effective_from is in the past or not later"
            " than every existing version (invalid_effective_from)."
        ),
    },
)
async def post_cancellation_rule(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    body: CancellationRuleCreate,
    uow: UnitOfWorkDep,
) -> CancellationRuleItem:
    """Publish a new rule version, in force from `effective_from` (now when
    omitted). Versions are never edited: to change an upcoming one, delete
    it and create another. Cancellations already decided keep their
    outcome."""
    return await create_cancellation_rule(uow.session, body)


@router.delete(
    "/cancellation-rules/{rule_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such rule version (not_found)."),
        409: problem_response_doc(
            "The version is in force or in the past; only an upcoming version"
            " can be deleted (policy_version_in_effect)."
        ),
    },
)
async def remove_cancellation_rule(
    principal: Annotated[Principal, require(BILLING_MANAGE)],
    rule_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming rule version."""
    await delete_cancellation_rule(uow.session, rule_id)
