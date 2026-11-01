"""Tutor compensation routes (#105 G; ``app.workflows.payroll_compensation``;
ADR-0017 E1, E12).

Reads need ``payroll.view``, writes ``payroll.manage`` (Owners and Finance
Staff; not delegable). The compensation read is ``authenticated()`` with a
record policy, so a tutor reads their own; anyone else gets the same ``404``
as a missing id. No reauthentication and no ``Idempotency-Key``: these rows
are configuration (#105 D4).

- ``GET /api/v1/payroll/tutors``: tutors with their pay summary, no contact
  data.
- ``GET /api/v1/payroll/tutors/{tutor_user_id}/compensation``.
- ``POST /api/v1/payroll/tutors/{tutor_user_id}/pay-terms`` and ``/rates``.
- ``DELETE /api/v1/payroll/tutors/{tutor_user_id}/pay-terms/{terms_id}`` and
  ``/rates/{rate_id}``.

There is no ``PATCH``: a correction is a delete and a new version (#105 E4).
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Query, Response

from app.core.authz import (
    PERMISSION_RESPONSES,
    Principal,
    authenticated,
    require,
)
from app.core.errors import problem_response_doc
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.identity.schemas import UserState
from app.modules.payroll.schemas import (
    CompensationRateCreate,
    CompensationRateItem,
    PayTermsCreate,
    PayTermsItem,
)
from app.workflows import payroll_compensation
from app.workflows.payroll_compensation_schemas import (
    PayrollTutorItem,
    TutorCompensation,
)

PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_MANAGE: Final = "payroll.manage"

router = APIRouter(prefix="/api/v1/payroll/tutors", tags=["payroll"])

_NOT_FOUND = problem_response_doc("No such tutor (not_found).")
_VERSION_NOT_FOUND = problem_response_doc(
    "No such tutor, or no such version of this tutor (not_found)."
)
_WRITE_RESPONSES: Final = {
    **PERMISSION_RESPONSES,
    403: problem_response_doc(
        "Not allowed (permission_denied), or the caller's own compensation"
        " (payroll_own_compensation; only an Owner may write their own)."
    ),
}


@router.get(
    "",
    responses={
        **PERMISSION_RESPONSES,
        422: problem_response_doc(
            "An unknown state, q longer than 100 characters, or a limit or"
            " offset out of range (validation_error)."
        ),
    },
)
async def list_payroll_tutors(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    page: OffsetPagination,
    uow: UnitOfWorkDep,
    q: Annotated[
        str | None,
        Query(
            max_length=100,
            description=(
                "Part of the username or display name (not case-sensitive;"
                " accents are not folded)."
            ),
        ),
    ] = None,
    state: Annotated[UserState | None, Query(description="One user state.")] = None,
) -> OffsetPage[PayrollTutorItem]:
    """The users holding the tutor role, in any state, by display name, then
    username, with the terms and rated class kinds in effect today."""
    return await payroll_compensation.list_tutors(
        uow.session, q=(q or "").strip() or None, state=state, page=page
    )


@router.get(
    "/{tutor_user_id}/compensation",
    responses={**PERMISSION_RESPONSES, 404: _NOT_FOUND},
)
async def get_tutor_compensation(
    principal: Annotated[Principal, authenticated()],
    tutor_user_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> TutorCompensation:
    """A tutor's pay terms and rates, for a holder of payroll.view or the
    tutor themselves. Terms are newest first; rates are by class kind, then
    newest first."""
    return await payroll_compensation.get_compensation(
        uow.session, principal, tutor_user_id
    )


@router.post(
    "/{tutor_user_id}/pay-terms",
    status_code=201,
    responses={
        **_WRITE_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "This tutor has terms from this date (pay_terms_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, an unknown frequency, the salary (money_invalid,"
            " money_precision, money_out_of_range, salary_not_positive) or a"
            " salary without a frequency (salary_without_frequency)."
        ),
    },
)
async def add_pay_terms(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    body: PayTermsCreate,
    uow: UnitOfWorkDep,
) -> PayTermsItem:
    """Add a terms version from any date. A null frequency means the tutor
    is not on payroll from that date."""
    return await payroll_compensation.add_pay_terms(
        uow.session, principal, tutor_user_id, body
    )


@router.post(
    "/{tutor_user_id}/rates",
    status_code=201,
    responses={
        **_WRITE_RESPONSES,
        404: _NOT_FOUND,
        409: problem_response_doc(
            "This tutor has a rate for this class kind from this date"
            " (compensation_rate_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, an unknown class kind or rate model, the rate"
            " (rate_invalid, rate_precision, rate_out_of_range, rate_negative),"
            " only one of rate model and rate (rate_model_rate_mismatch) or"
            " per_course on another kind (rate_model_not_allowed)."
        ),
    },
)
async def add_compensation_rate(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    body: CompensationRateCreate,
    uow: UnitOfWorkDep,
) -> CompensationRateItem:
    """Add a rate version from any date. A null rate model and rate mean no
    rate for this class kind from that date."""
    return await payroll_compensation.add_compensation_rate(
        uow.session, principal, tutor_user_id, body
    )


@router.delete(
    "/{tutor_user_id}/pay-terms/{terms_id}",
    status_code=204,
    responses={
        **_WRITE_RESPONSES,
        404: _VERSION_NOT_FOUND,
        409: problem_response_doc(
            "A finalized payroll period uses this version (pay_terms_in_use)."
        ),
    },
)
async def delete_pay_terms(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    terms_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> Response:
    """Delete a terms version, whatever its date, until a finalized period
    uses it."""
    await payroll_compensation.delete_pay_terms(
        uow.session, principal, tutor_user_id, terms_id
    )
    return Response(status_code=204)


@router.delete(
    "/{tutor_user_id}/rates/{rate_id}",
    status_code=204,
    responses={
        **_WRITE_RESPONSES,
        404: _VERSION_NOT_FOUND,
        409: problem_response_doc(
            "A finalized payroll period uses this version (compensation_rate_in_use)."
        ),
    },
)
async def delete_compensation_rate(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    rate_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> Response:
    """Delete a rate version, whatever its date, until a finalized period
    uses it."""
    await payroll_compensation.delete_compensation_rate(
        uow.session, principal, tutor_user_id, rate_id
    )
    return Response(status_code=204)
