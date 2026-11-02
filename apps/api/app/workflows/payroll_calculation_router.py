"""Payroll calculation routes (#107 H; ``app.workflows.payroll_calculation``;
ADR-0017 E9, E12; #107 E10).

- ``POST /api/v1/payroll/periods/{period_id}/recalculate``:
  ``require("payroll.prepare")``. Repeatable by nature, so no
  ``Idempotency-Key``, and no reauthentication: it applies no judgment.
- ``GET /api/v1/payroll/periods/{period_id}/lines``:
  ``require("payroll.view")``. Not paginated: one period.
- ``GET /api/v1/payroll/unresolved-work``: ``require("payroll.view")``, cursor
  pagination (#443). Approved work that can't be paid, and why: no amount,
  minutes or student data.

Both permissions are held by Owners and Finance Staff. Administrators get
``403`` here, also with ``payroll.finalize`` delegated, as do Coordinators,
Tutors (their own period too: #113 is their read), Students and Guardians.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage, CursorPagination
from app.core.uow import UnitOfWorkDep
from app.modules.payroll.schemas import (
    PayrollPeriodLines,
    PayrollRecalculation,
    UnresolvedReason,
    UnresolvedWorkItem,
)
from app.workflows import payroll_calculation

PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_PREPARE: Final = "payroll.prepare"

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])

_NOT_FOUND = problem_response_doc("No such period (not_found).")


@router.post(
    "/periods/{period_id}/recalculate",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The period isn't open (payroll_period_not_open, with"
            " details.status), or a line or the total is beyond the money"
            " range (payroll_amount_out_of_range, with"
            " details.class_session_id for a line)."
        ),
    },
)
async def recalculate_period(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_PREPARE)],
    uow: UnitOfWorkDep,
) -> PayrollRecalculation:
    """Rebuild the session, salary and pay item lines of an open period from
    approved work, approved pay events, the tutor's terms and their pay items.
    It may run before the period ends, as a preview. `line_count` and `total`
    describe all the period's lines, as `GET …/lines` does, and the total may be
    negative."""
    return await payroll_calculation.recalculate(uow.session, period_id)


@router.get(
    "/periods/{period_id}/lines",
    responses={
        **PERMISSION_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "The lines sum beyond the money range"
            " (payroll_amount_out_of_range): end or delete a pay item, then"
            " recalculate."
        ),
    },
)
async def get_period_lines(
    period_id: uuid.UUID,
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    uow: UnitOfWorkDep,
) -> PayrollPeriodLines:
    """The period and its lines, by service date, then session, then salary
    piece, then pay item, with their total."""
    return await payroll_calculation.read_lines(uow.session, period_id)


@router.get(
    "/unresolved-work",
    responses={
        **PERMISSION_RESPONSES,
        400: problem_response_doc("The cursor isn't valid (invalid_cursor)."),
    },
)
async def list_unresolved_work(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    uow: UnitOfWorkDep,
    page: CursorPagination,
    tutor_user_id: uuid.UUID | None = None,
    reason: UnresolvedReason | None = None,
) -> CursorPage[UnresolvedWorkItem]:
    """Approved work that can't be paid, newest service date first, then
    session. A ``blocking`` item stops ``prepare`` of a period ending on or
    after its date; ``before_first_period`` work doesn't, and Finance pays it
    with a one-time item."""
    return await payroll_calculation.list_unresolved_work(
        uow.session, page=page, tutor_user_id=tutor_user_id, reason=reason
    )
