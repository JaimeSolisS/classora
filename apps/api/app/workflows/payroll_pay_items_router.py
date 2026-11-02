"""Bonus and deduction routes (#109 D, F; ``app.workflows.payroll_pay_items``;
ADR-0017 E8, E12, its #109 notes).

Reads need ``payroll.view``, writes ``payroll.manage`` (Owners and Finance Staff;
not delegable). Everyone else gets ``403``, Administrators with
``payroll.finalize`` delegated and a tutor asking for their own items too: a
tutor reads finalized lines through #113. No reauthentication (#109 E6).

- ``GET``, ``POST /api/v1/payroll/pay-item-types`` and ``PATCH …/{type_id}``.
  There is no ``DELETE``: ``is_active`` retires a type. The name is a natural
  key, so no ``Idempotency-Key``.
- ``GET /api/v1/payroll/tutors/{tutor_user_id}/pay-items``.
- ``POST`` there, with an ``Idempotency-Key`` (#109 E5): an item has no natural
  key, so two clicks would be two bonuses. Check order: ``401`` → ``403
  csrf_failed`` → ``403 permission_denied`` → ``400
  idempotency_key_required|invalid`` → replay / ``422 idempotency_key_reused`` →
  ``422`` (the body) → ``403 payroll_own_compensation`` → ``404`` → the rules.
  The route never commits; it ends with ``idem.respond``.
- ``PATCH …/{item_id}`` sets ``effective_to`` only, ``DELETE …/{item_id}``.
"""

import uuid
from typing import Annotated, Final

from fastapi import APIRouter, Response

from app.core.authz import PERMISSION_RESPONSES, Principal, require
from app.core.errors import problem_response_doc
from app.core.idempotency import IDEMPOTENCY_RESPONSES, IdempotentRequest, idempotent
from app.core.pagination import OffsetPage, OffsetPagination
from app.core.uow import UnitOfWorkDep
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayItemCreate,
    PayItemDetail,
    PayItemTypeCreate,
    PayItemTypeItem,
    PayItemTypeUpdate,
    PayItemUpdate,
)
from app.workflows import payroll_pay_items

PAYROLL_VIEW: Final = "payroll.view"
PAYROLL_MANAGE: Final = "payroll.manage"

router = APIRouter(prefix="/api/v1/payroll", tags=["payroll"])

_TYPES: Final = "/pay-item-types"
_ITEMS: Final = "/tutors/{tutor_user_id}/pay-items"
_NO_TUTOR = problem_response_doc("No such tutor (not_found).")
_NO_ITEM = problem_response_doc(
    "No such tutor, or no such item of this tutor (not_found)."
)
_NO_TYPE = problem_response_doc("No such type (not_found).")
_WRITE_RESPONSES: Final = {
    **PERMISSION_RESPONSES,
    403: problem_response_doc(
        "Not allowed (permission_denied), or the caller's own pay"
        " (payroll_own_compensation; only an Owner may write their own)."
    ),
}


@router.get(_TYPES, responses=PERMISSION_RESPONSES)
async def list_pay_item_types(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    uow: UnitOfWorkDep,
) -> list[PayItemTypeItem]:
    """Every bonus and deduction type, active first, then by name."""
    return await payroll.list_pay_item_types(uow.session)


@router.post(
    _TYPES,
    status_code=201,
    responses={
        **PERMISSION_RESPONSES,
        409: problem_response_doc(
            "A type has this name, ignoring case (pay_item_type_exists)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error): a missing or unknown"
            " field, an unknown direction, or the name (text_empty,"
            " text_too_long, text_invalid)."
        ),
    },
)
async def create_pay_item_type(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    body: PayItemTypeCreate,
    uow: UnitOfWorkDep,
) -> PayItemTypeItem:
    """Add a type. Its direction can't change later."""
    return await payroll.add_pay_item_type(uow.session, body)


@router.patch(
    f"{_TYPES}/{{type_id}}",
    responses={
        **PERMISSION_RESPONSES,
        404: _NO_TYPE,
        409: problem_response_doc(
            "The version is stale (version_conflict), a type has this name"
            " (pay_item_type_exists), or a recurring item of this type is still"
            " running (pay_item_type_in_use)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error), direction included: it"
            " can't change."
        ),
    },
)
async def update_pay_item_type(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    type_id: uuid.UUID,
    body: PayItemTypeUpdate,
    uow: UnitOfWorkDep,
) -> PayItemTypeItem:
    """Rename, deactivate or reactivate a type. A new name shows on every line
    that uses it. Deactivating needs every recurring item of the type to have
    ended before the center's today."""
    return await payroll_pay_items.update_type(uow.session, type_id, body)


@router.get(
    _ITEMS,
    responses={
        **PERMISSION_RESPONSES,
        404: _NO_TUTOR,
        422: problem_response_doc("A limit or offset out of range (validation_error)."),
    },
)
async def list_pay_items(
    principal: Annotated[Principal, require(PAYROLL_VIEW)],
    tutor_user_id: uuid.UUID,
    page: OffsetPagination,
    uow: UnitOfWorkDep,
) -> OffsetPage[PayItemDetail]:
    """A tutor's bonuses and deductions, newest start first."""
    return await payroll_pay_items.list_items(uow.session, tutor_user_id, page)


@router.post(
    _ITEMS,
    status_code=201,
    response_model=PayItemDetail,
    responses={
        **_WRITE_RESPONSES,
        **IDEMPOTENCY_RESPONSES,
        404: _NO_TUTOR,
        409: problem_response_doc("The type is inactive (pay_item_type_inactive)."),
        422: problem_response_doc(
            "The body is not valid (validation_error: amount_not_positive,"
            " money_*, text_empty, text_too_long, text_invalid,"
            " effective_to_not_allowed,"
            " effective_to_before_from, …); the type doesn't exist"
            " (pay_item_type_unknown); or the Idempotency-Key was already used"
            " for a different request (idempotency_key_reused)."
        ),
    },
)
async def create_pay_item(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    idem: Annotated[IdempotentRequest, idempotent()],
    tutor_user_id: uuid.UUID,
    body: PayItemCreate,
    uow: UnitOfWorkDep,
) -> Response:
    """Give a tutor a bonus or deduction. A one-time item lands in the earliest
    not-finalized period ending on or after its date. A recurring amount is
    paid in full in every period overlapping its dates. Each shows on the next
    recalculation. Send a new `Idempotency-Key` per item and the same one to
    retry it."""
    item = await payroll_pay_items.add_item(uow.session, principal, tutor_user_id, body)
    return await idem.respond(item, status_code=201)


@router.patch(
    f"{_ITEMS}/{{item_id}}",
    responses={
        **_WRITE_RESPONSES,
        404: _NO_ITEM,
        409: problem_response_doc(
            "The version is stale (version_conflict), or the item is one-time"
            " (pay_item_not_recurring)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error), any field but version"
            " and effective_to included; the end is before the item's start"
            " (pay_item_end_before_start) or before a finalized period that"
            " paid it (pay_item_end_before_paid_period)."
        ),
    },
)
async def end_pay_item(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PayItemUpdate,
    uow: UnitOfWorkDep,
) -> PayItemDetail:
    """Set or clear a recurring item's end. Any other change is a delete and a
    new item."""
    return await payroll_pay_items.end_item(
        uow.session, principal, tutor_user_id, item_id, body
    )


@router.delete(
    f"{_ITEMS}/{{item_id}}",
    status_code=204,
    responses={
        **_WRITE_RESPONSES,
        404: _NO_ITEM,
        409: problem_response_doc(
            "A prepared or finalized payroll period uses this item (pay_item_in_use)."
        ),
    },
)
async def delete_pay_item(
    principal: Annotated[Principal, require(PAYROLL_MANAGE)],
    tutor_user_id: uuid.UUID,
    item_id: uuid.UUID,
    uow: UnitOfWorkDep,
) -> Response:
    """Delete an item, with its lines in open periods, until a prepared or
    finalized period uses it."""
    await payroll_pay_items.delete_item(uow.session, principal, tutor_user_id, item_id)
    return Response(status_code=204)
