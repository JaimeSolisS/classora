"""The saved export setup routes (#125 B; ``_docs/api.md`` Saved export
setups).

All four are ``authenticated()`` plus the exports' :func:`exporter`: ``403
permission_denied`` unless the caller may export some dataset, before the
body. Tutors, students and guardians get ``403`` everywhere. There is no run
route (#125 E4): running a setup is ``POST /api/v1/exports`` with its stored
``parameters``, under every export check for the runner.

The responses leave out what the stored ``parameters`` leave out
(``response_model_exclude_unset``), so they are returned exactly as stored.
"""

import uuid
from typing import Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.reporting.exports.router import Exporter
from app.modules.reporting.setups import service
from app.modules.reporting.setups.schemas import (
    ReportSetupCreate,
    ReportSetupItem,
    ReportSetupList,
    ReportSetupUpdate,
)

router = APIRouter(prefix="/api/v1/report-setups", tags=["report setups"])

_SETUP: Final = "/{setup_id}"
_NOT_FOUND: Final = problem_response_doc(
    "No such setup, or one the caller may not see: another user's private"
    " setup, or one shared with a role or a dataset the caller lacks"
    " (not_found)."
)
_INVALID: Final = problem_response_doc(
    "The body is not valid: the name, the shared role or any export"
    " parameter (validation_error); a date range reversed or over 366 days"
    " (export_range_invalid)."
)


@router.get("", response_model_exclude_unset=True, responses={**PERMISSION_RESPONSES})
async def list_report_setups(
    principal: Exporter, uow: UnitOfWorkDep
) -> ReportSetupList:
    """Your saved export setups, and those shared with one of your roles
    whose data you may export, by name."""
    return await service.list_setups(uow.session, principal=principal)


@router.post(
    "",
    status_code=201,
    response_model_exclude_unset=True,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "You already have 50 setups (report_setup_limit_reached;"
            " details.max), or one with this name (report_setup_name_taken)."
        ),
        422: _INVALID,
    },
)
async def create_report_setup(
    principal: Exporter, body: ReportSetupCreate, uow: UnitOfWorkDep
) -> ReportSetupItem:
    """Save an export's parameters under a name, optionally shared with one
    staff role. You must be allowed to export its dataset."""
    return await service.create_setup(uow.session, principal=principal, body=body)


@router.patch(
    _SETUP,
    response_model_exclude_unset=True,
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The setup changed since it was read (version_conflict), or you"
            " have another setup with this name (report_setup_name_taken)."
        ),
        422: _INVALID,
    },
)
async def update_report_setup(
    principal: Exporter,
    setup_id: uuid.UUID,
    body: ReportSetupUpdate,
    uow: UnitOfWorkDep,
) -> ReportSetupItem:
    """Rename, re-share (`shared_role: null` makes it private) or replace the
    parameters of one of your setups. Only its creator may change it."""
    return await service.update_setup(
        uow.session, principal=principal, setup_id=setup_id, body=body
    )


@router.delete(
    _SETUP,
    status_code=204,
    response_class=Response,
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def delete_report_setup(
    principal: Exporter, setup_id: uuid.UUID, uow: UnitOfWorkDep
) -> Response:
    """Delete one of your setups. Only its creator may delete it."""
    await service.delete_setup(uow.session, principal=principal, setup_id=setup_id)
    return Response(status_code=204)
