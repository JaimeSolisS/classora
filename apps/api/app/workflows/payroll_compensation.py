"""Tutor compensation (#105 G): the cross-module steps behind
``app.workflows.payroll_compensation_router``.

The payroll module owns the versions and their rules. This workflow adds what
needs other modules (ADR-0002): the tutor (``identity.get_role_member``, in
any account state, so a leaver who is archived can still get a stop row) and
the center's local today (``administration.center_today``).

Check order, after the route's access rule and the body:

- reads: the record policy, then the tutor (both the same ``404``);
- writes: the own-pay rule (``403 payroll_own_compensation``), then the
  tutor, then the payroll rules.

Statements: the list page is six, whatever its size (the zone, the three of
``list_role_members``, the two summaries).
"""

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal, RecordNotFoundError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.identity.schemas import RoleMember
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import (
    CompensationRateCreate,
    CompensationRateItem,
    CompensationSummary,
    PayTermsCreate,
    PayTermsItem,
)
from app.workflows.payroll_compensation_schemas import (
    CompensationTutor,
    PayrollTutorItem,
    TutorCompensation,
)

TUTOR = "tutor"


async def _tutor(session: AsyncSession, tutor_user_id: uuid.UUID) -> RoleMember:
    member = await identity.get_role_member(session, TUTOR, tutor_user_id)
    if member is None:
        raise RecordNotFoundError()
    return member


async def _write_target(
    session: AsyncSession, principal: Principal, tutor_user_id: uuid.UUID
) -> None:
    if not payroll.can_write_compensation(principal, tutor_user_id):
        raise payroll.PayrollOwnCompensationError()
    await _tutor(session, tutor_user_id)


def _item(member: RoleMember, summary: CompensationSummary) -> PayrollTutorItem:
    return PayrollTutorItem(
        user_id=member.id,
        display_name=member.display_name,
        username=member.username,
        state=member.state,
        current_terms=summary.current_terms,
        current_rate_kinds=list(summary.current_rate_kinds),
    )


async def list_tutors(
    session: AsyncSession,
    *,
    q: str | None,
    state: str | None,
    page: OffsetParams,
) -> OffsetPage[PayrollTutorItem]:
    """``GET /api/v1/payroll/tutors`` (G1)."""
    today = await administration.center_today(session)
    members = await identity.list_role_members(
        session, TUTOR, q=q, state=state, page=page
    )
    summaries = (
        await payroll.compensation_summaries(
            session, [member.id for member in members.items], today=today
        )
        if members.items
        else {}
    )
    return OffsetPage[PayrollTutorItem](
        items=[
            _item(member, summaries.get(member.id, CompensationSummary()))
            for member in members.items
        ],
        total=members.total,
        limit=members.limit,
        offset=members.offset,
    )


async def get_compensation(
    session: AsyncSession, principal: Principal, tutor_user_id: uuid.UUID
) -> TutorCompensation:
    """``GET /api/v1/payroll/tutors/{id}/compensation`` (G2)."""
    if not payroll.can_view_compensation(principal, tutor_user_id):
        raise RecordNotFoundError()
    member = await _tutor(session, tutor_user_id)
    today = await administration.center_today(session)
    return TutorCompensation(
        tutor=CompensationTutor(
            user_id=member.id, display_name=member.display_name, state=member.state
        ),
        today=today,
        terms=await payroll.list_pay_terms(session, tutor_user_id, today=today),
        rates=await payroll.list_compensation_rates(
            session, tutor_user_id, today=today
        ),
    )


async def _terms_item(
    session: AsyncSession, tutor_user_id: uuid.UUID, terms_id: uuid.UUID, today: date
) -> PayTermsItem:
    versions = await payroll.list_pay_terms(session, tutor_user_id, today=today)
    return next(item for item in versions if item.id == terms_id)


async def _rate_item(
    session: AsyncSession, tutor_user_id: uuid.UUID, rate_id: uuid.UUID, today: date
) -> CompensationRateItem:
    versions = await payroll.list_compensation_rates(
        session, tutor_user_id, today=today
    )
    return next(item for item in versions if item.id == rate_id)


async def add_pay_terms(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    body: PayTermsCreate,
) -> PayTermsItem:
    """``POST /api/v1/payroll/tutors/{id}/pay-terms`` (G4)."""
    await _write_target(session, principal, tutor_user_id)
    terms_id = await payroll.add_pay_terms(
        session,
        tutor_user_id,
        effective_from=body.effective_from,
        pay_frequency=body.pay_frequency,
        salary_amount=body.salary_amount,
    )
    today = await administration.center_today(session)
    return await _terms_item(session, tutor_user_id, terms_id, today)


async def add_compensation_rate(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    body: CompensationRateCreate,
) -> CompensationRateItem:
    """``POST /api/v1/payroll/tutors/{id}/rates`` (G5)."""
    await _write_target(session, principal, tutor_user_id)
    rate_id = await payroll.add_compensation_rate(
        session,
        tutor_user_id,
        class_kind=body.class_kind,
        effective_from=body.effective_from,
        rate_model=body.rate_model,
        rate=body.rate,
    )
    today = await administration.center_today(session)
    return await _rate_item(session, tutor_user_id, rate_id, today)


async def delete_pay_terms(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    terms_id: uuid.UUID,
) -> None:
    """``DELETE /api/v1/payroll/tutors/{id}/pay-terms/{terms_id}`` (G6)."""
    await _write_target(session, principal, tutor_user_id)
    await payroll.delete_pay_terms(session, tutor_user_id, terms_id)


async def delete_compensation_rate(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    rate_id: uuid.UUID,
) -> None:
    """``DELETE /api/v1/payroll/tutors/{id}/rates/{rate_id}`` (G6)."""
    await _write_target(session, principal, tutor_user_id)
    await payroll.delete_compensation_rate(session, tutor_user_id, rate_id)
