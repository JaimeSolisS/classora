"""Session pay rules (#441 C4): the cross-module step behind
``app.workflows.payroll_pay_rules_router``.

The payroll module owns the versions and their rules. This workflow adds the
center's local today (``administration.center_today``, ADR-0002), which
decides what is current, upcoming or past, and which dates a new version may
take.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    SessionPayRuleCreate,
    SessionPayRuleItem,
    SessionPayRuleOverview,
)


async def get_overview(session: AsyncSession) -> SessionPayRuleOverview:
    """``GET /api/v1/payroll/pay-rules`` (D1): seven statements, the zone and
    the six of the overview."""
    today = await administration.center_today(session)
    return await payroll.get_session_pay_rule_overview(session, today=today)


async def create_rule(
    session: AsyncSession, body: SessionPayRuleCreate
) -> SessionPayRuleItem:
    """``POST /api/v1/payroll/pay-rules`` (D2)."""
    today = await administration.center_today(session)
    return await payroll.create_session_pay_rule(session, body, today=today)


async def delete_rule(session: AsyncSession, rule_id: uuid.UUID) -> None:
    """``DELETE /api/v1/payroll/pay-rules/{rule_id}`` (D3)."""
    today = await administration.center_today(session)
    await payroll.delete_session_pay_rule(session, rule_id, today=today)
