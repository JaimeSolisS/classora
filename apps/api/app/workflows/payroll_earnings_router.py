"""A tutor's earnings and payslip routes (#113 C; ``app.workflows.payroll_earnings``;
ADR-0017 E12, E14).

- ``GET /api/v1/payroll/tutors/{tutor_user_id}/earnings``: the tutor's
  finalized periods, offset-paginated, newest ``ends_on`` first.
- ``GET /api/v1/payroll/tutors/{tutor_user_id}/earnings/{period_id}``: one
  finalized period with its lines, summary, payments and payslip.
- ``GET /api/v1/payroll/tutors/{tutor_user_id}/payslips/{payslip_id}/pdf``:
  ``200 application/pdf`` as an attachment named ``<number>.pdf``,
  ``Cache-Control: no-store``.

All three are ``authenticated()`` with the compensation record policy (the
tutor, or ``payroll.view``). Check order ``401`` → ``403
privacy_notice_required`` → ``422`` (a malformed id or query) → ``404
not_found`` (anyone else, an unknown or non-tutor id, another tutor's period or
payslip, a period that isn't finalized) → ``200``. No key, no reauthentication,
no audit, no rate limit (the P9 precedent).

The PDF is rendered on every request (never stored, owner decision 2026-10-21)
in the payslip's stored language, whoever downloads it. The read transaction is
committed before rendering, which runs in a worker thread
(``anyio.to_thread.run_sync``), so no connection is held while WeasyPrint works.
"""

import uuid
from typing import Annotated, Final

import anyio
from fastapi import APIRouter, Response

from app.core.authz import AUTH_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.workflows import payroll_earnings
from app.workflows.payroll_earnings_schemas import (
    EarningsPeriodDetail,
    EarningsPeriodItem,
)

PDF: Final = "application/pdf"

router = APIRouter(prefix="/api/v1/payroll/tutors", tags=["payroll"])

_PRIVACY: Final = problem_response_doc(
    "The privacy notice must be accepted first (privacy_notice_required)."
)


@router.get(
    "/{tutor_user_id}/earnings",
    responses={
        **AUTH_RESPONSES,
        403: _PRIVACY,
        404: problem_response_doc(
            "No such tutor, or one whose pay you can't read (not_found)."
        ),
        422: problem_response_doc(
            "The id is not a UUID, or a limit or offset out of range"
            " (validation_error)."
        ),
    },
)
async def list_tutor_earnings(
    principal: Annotated[Principal, authenticated()],
    tutor_user_id: uuid.UUID,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
) -> OffsetPage[EarningsPeriodItem]:
    """A tutor's finalized pay periods, newest first, with what was paid and
    the payslip. For the tutor themselves or a holder of payroll.view. Periods
    still open or prepared are never listed."""
    return await payroll_earnings.list_earnings(
        uow.session, principal, tutor_user_id, page=page
    )


@router.get(
    "/{tutor_user_id}/earnings/{period_id}",
    responses={
        **AUTH_RESPONSES,
        403: _PRIVACY,
        404: problem_response_doc(
            "No such finalized period of this tutor, or a tutor whose pay you"
            " can't read (not_found)."
        ),
        422: problem_response_doc("An id is not a UUID (validation_error)."),
    },
)
async def get_tutor_earnings_period(
    principal: Annotated[Principal, authenticated()],
    tutor_user_id: uuid.UUID,
    period_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> EarningsPeriodDetail:
    """One finalized pay period of a tutor: its lines (sessions, salary,
    bonuses, deductions, adjustments), the summary, the payments (reversed
    ones marked) and the payslip. For the tutor themselves or a holder of
    payroll.view."""
    return await payroll_earnings.read_earnings(
        uow.session, principal, tutor_user_id, period_id
    )


@router.get(
    "/{tutor_user_id}/payslips/{payslip_id}/pdf",
    response_class=Response,
    responses={
        200: {
            "description": "The payslip as a PDF attachment.",
            "content": {PDF: {"schema": {"type": "string", "format": "binary"}}},
        },
        **AUTH_RESPONSES,
        403: _PRIVACY,
        404: problem_response_doc(
            "No such payslip of this tutor, or a tutor whose pay you can't read"
            " (not_found)."
        ),
        422: problem_response_doc("An id is not a UUID (validation_error)."),
        500: problem_response_doc(
            "The payslip could not be rendered (internal_error)."
        ),
    },
)
async def download_payslip(
    principal: Annotated[Principal, authenticated()],
    tutor_user_id: uuid.UUID,
    payslip_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> Response:
    """Download a payslip as a PDF (not a tax document: not a CFDI de nómina),
    in the language it was issued in. For the tutor themselves or a holder of
    payroll.view."""
    stored = await payroll_earnings.load_payslip(
        uow.session, principal, tutor_user_id, payslip_id
    )
    # End the read transaction before the CPU-bound render (C4), so no
    # connection waits on it; the Unit of Work then commits nothing.
    await uow.session.commit()
    file = await anyio.to_thread.run_sync(payroll_earnings.render, stored)
    return Response(
        content=file.data,
        media_type=PDF,
        headers={
            "Content-Disposition": f'attachment; filename="{file.filename}"',
            "Cache-Control": "no-store",
        },
    )
