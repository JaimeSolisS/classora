"""Bonuses and deductions (#109 D, F): the cross-module steps behind
``app.workflows.payroll_pay_items_router``.

The payroll module owns types and items. This workflow adds what needs other
modules (ADR-0002): the tutor, in any account state, and the center's local
today, which decides whether a recurring item is still running.

Check order of an item write, after the route's access rule and the body: the
own-pay rule (``403 payroll_own_compensation``), then the tutor (``404``), then
the rules of :mod:`~app.modules.payroll.pay_items`. An id that isn't a tutor and
an item of another tutor are the same ``404``.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    PayItemCreate,
    PayItemDetail,
    PayItemTypeItem,
    PayItemTypeUpdate,
    PayItemUpdate,
)

TUTOR = "tutor"


async def _tutor(session: AsyncSession, tutor_user_id: uuid.UUID) -> None:
    if await identity.get_role_member(session, TUTOR, tutor_user_id) is None:
        raise RecordNotFoundError()


async def _write_target(
    session: AsyncSession, principal: Principal, tutor_user_id: uuid.UUID
) -> None:
    if not payroll.can_write_compensation(principal, tutor_user_id):
        raise payroll.PayrollOwnCompensationError()
    await _tutor(session, tutor_user_id)


async def update_type(
    session: AsyncSession, type_id: uuid.UUID, body: PayItemTypeUpdate
) -> PayItemTypeItem:
    """``PATCH /api/v1/payroll/pay-item-types/{type_id}`` (D3, D4)."""
    today = await administration.center_today(session)
    return await payroll.change_pay_item_type(session, type_id, body, today=today)


async def list_items(
    session: AsyncSession, tutor_user_id: uuid.UUID, page: OffsetParams
) -> OffsetPage[PayItemDetail]:
    """``GET /api/v1/payroll/tutors/{id}/pay-items`` (F1)."""
    await _tutor(session, tutor_user_id)
    return await payroll.list_pay_items(session, tutor_user_id, page)


async def add_item(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    body: PayItemCreate,
) -> PayItemDetail:
    """``POST /api/v1/payroll/tutors/{id}/pay-items`` (F2)."""
    await _write_target(session, principal, tutor_user_id)
    item_id = await payroll.add_pay_item(
        session,
        tutor_user_id,
        pay_item_type_id=body.pay_item_type_id,
        description=body.description,
        amount=body.amount,
        recurrence=body.recurrence,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
    )
    return await payroll.get_pay_item(session, tutor_user_id, item_id)


async def end_item(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    item_id: uuid.UUID,
    body: PayItemUpdate,
) -> PayItemDetail:
    """``PATCH /api/v1/payroll/tutors/{id}/pay-items/{item_id}`` (F5)."""
    await _write_target(session, principal, tutor_user_id)
    await payroll.end_pay_item(session, tutor_user_id, item_id, body)
    return await payroll.get_pay_item(session, tutor_user_id, item_id)


async def delete_item(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    item_id: uuid.UUID,
) -> None:
    """``DELETE /api/v1/payroll/tutors/{id}/pay-items/{item_id}`` (F6)."""
    await _write_target(session, principal, tutor_user_id)
    await payroll.delete_pay_item(session, tutor_user_id, item_id)
