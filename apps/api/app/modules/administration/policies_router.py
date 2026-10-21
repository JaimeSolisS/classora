"""Scheduling policy routes (#61; ``app.modules.administration.policy_versions``).

- ``GET /api/v1/scheduling-policies``: every signed-in user
  (``authenticated()``); families and tutors need the notice rules, and
  nothing in it is personal data.
- ``POST /api/v1/scheduling-policies`` and
  ``DELETE /api/v1/scheduling-policies/{policy_id}``:
  ``require("settings.manage")`` (Owners and Administrators).

Check order of the ``POST``: ``401`` → ``csrf_failed`` → ``permission_denied``
→ ``422`` (body) → ``409 version_conflict`` → ``422 invalid_effective_from``.
Of the ``DELETE``: ``401`` → ``csrf_failed`` → ``permission_denied`` → ``404``
→ ``409 policy_version_in_effect``.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Response

from app.core.authz import (
    AUTH_RESPONSES,
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.administration import service
from app.modules.administration.schemas import (
    SchedulingPolicyCreate,
    SchedulingPolicyItem,
    SchedulingPolicyOverview,
)

router = APIRouter(prefix="/api/v1", tags=["scheduling-policies"])

SETTINGS_MANAGE = "settings.manage"


@router.get("/scheduling-policies", responses=AUTH_RESPONSES)
async def get_scheduling_policies(
    principal: Annotated[Principal, authenticated()],
    uow: UnitOfWorkDep,
) -> SchedulingPolicyOverview:
    """The scheduling policy in force now, the upcoming versions (oldest
    first) and the past ones (newest first, at most 50)."""
    return await service.get_scheduling_policy_overview(uow.session)


@router.post(
    "/scheduling-policies",
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "based_on_id is not the latest version: the policy changed since"
            " it was loaded (version_conflict); reload and retry."
        ),
        422: problem_response_doc(
            "The body is not valid: a missing or out-of-range value, a naive"
            " effective_from or an unknown field (validation_error); or"
            " effective_from is in the past or not later than every existing"
            " version (invalid_effective_from)."
        ),
    },
)
async def create_scheduling_policy(
    principal: Annotated[Principal, require(SETTINGS_MANAGE)],
    body: SchedulingPolicyCreate,
    uow: UnitOfWorkDep,
) -> SchedulingPolicyItem:
    """Publish a new policy version, in force from ``effective_from`` (now
    when omitted). Versions are never edited: to change an upcoming one,
    delete it and create another."""
    return await service.create_scheduling_policy(uow.session, body)


@router.delete(
    "/scheduling-policies/{policy_id}",
    status_code=204,
    response_class=Response,
    responses={
        **PERMISSION_RESPONSES,
        404: problem_response_doc("No such policy version (not_found)."),
        409: problem_response_doc(
            "The version is in force or in the past; only an upcoming version"
            " can be deleted (policy_version_in_effect)."
        ),
    },
)
async def delete_scheduling_policy(
    principal: Annotated[Principal, require(SETTINGS_MANAGE)],
    policy_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> None:
    """Delete an upcoming policy version."""
    await service.delete_scheduling_policy(uow.session, policy_id)
