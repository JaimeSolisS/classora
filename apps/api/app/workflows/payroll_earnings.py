"""Payslips and a tutor's earnings (#113; spec §2, §7; ADR-0017 E12, E14; ADR-0015):
the cross-module work behind ``app.workflows.payroll_earnings_router`` and the
payslip ``finalize`` issues.

The payroll module owns the payslips, the periods, the lines and the payments
(``payroll.service``); ``administration`` owns the center's settings and time
zone, and ``identity`` the tutor's name, role and language. This workflow joins
them (ADR-0002).

**Issuing** (:func:`issue_for`): ``payroll_workflow.finalize`` calls it last,
for a period with lines. It reads the center's settings, the tutor's name and
stored language (B1 step 1), then ``payroll.issue_payslip`` reads the lines and
takes the ``P`` number as the last lock. A fixed number of statements, whatever
the line count (B7).

**Who reads** (#113 E2, the ``/my-pay`` precedent): the compensation record
policy, ``payroll.can_view_compensation``: a ``payroll.view`` holder (Owner,
Finance Staff) any tutor, the tutor their own while holding the tutor role.
Anyone else, a delegated Administrator included, gets the same ``404
not_found`` as an unknown id. For a ``payroll.view`` holder, the id must be a
tutor's: a user holding the role, or one with a payroll period (a leaver who
lost the role is still read by Finance). Only ``finalized`` periods are read
(E3); a period or payslip that isn't this tutor's, or a period that isn't
``finalized``, is the same ``404``. No key, no reauthentication, no audit.

**Rendering** (:func:`render`) is synchronous and CPU-bound: the route runs it
in a worker thread after its read transaction ended. A failure (an unknown
template version included) is logged with the payslip id and the error type
only, never the snapshot, and answers the standard ``500 internal_error``.
"""

import logging
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import i18n
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import AppError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import PayrollPeriodFacts
from app.workflows.payroll_earnings_schemas import (
    EarningsPayment,
    EarningsPeriodDetail,
    EarningsPeriodItem,
    PayslipReference,
)

logger = logging.getLogger(__name__)

TUTOR = "tutor"


class PayslipRenderError(AppError):
    """The payslip could not be rendered (C4): the standard ``500``."""

    status = 500
    code = "internal_error"
    detail = "The server could not complete the request."


@dataclass(frozen=True)
class PayslipFile:
    """A rendered payslip: its file name and PDF bytes."""

    filename: str
    data: bytes


# --- Issuing (B) ---------------------------------------------------------------------


async def issue_for(
    session: AsyncSession, period: PayrollPeriodFacts
) -> payroll.IssuedPayslip:
    """The payslip of this just-finalized period with lines (B1). Never
    commits."""
    settings = await administration.get_business_settings(session)
    accounts = await identity.get_account_references(session, {period.tutor_user_id})
    preferences = await identity.get_user_preferences(session, period.tutor_user_id)
    return await payroll.issue_payslip(
        session,
        period,
        payroll.PayslipInputs(
            center=payroll.PayslipCenter(
                name=settings.center_name,
                address=settings.address,
                phone=settings.contact_phone,
                email=settings.contact_email,
            ),
            time_zone=settings.time_zone,
            tutor_display_name=accounts[period.tutor_user_id].display_name,
            language=i18n.resolve_language(preferences.language),
        ),
    )


# --- Reading (C) ---------------------------------------------------------------------


async def _visible_tutor(
    session: AsyncSession, principal: Principal, tutor_user_id: uuid.UUID
) -> None:
    """The record policy (module docstring), or ``404``."""
    if not payroll.can_view_compensation(principal, tutor_user_id):
        raise RecordNotFoundError()


async def _known_tutor(session: AsyncSession, tutor_user_id: uuid.UUID) -> None:
    """``404`` unless the user holds the tutor role or has a payroll period."""
    if await payroll.has_periods(session, tutor_user_id):
        return
    if await identity.get_role_member(session, TUTOR, tutor_user_id) is None:
        raise RecordNotFoundError()


def _reference(row: payroll.EarningsPeriodRow) -> PayslipReference | None:
    if row.payslip_id is None or row.payslip_number is None:
        return None
    return PayslipReference(id=row.payslip_id, number=row.payslip_number)


def _item(row: payroll.EarningsPeriodRow, zone: ZoneInfo) -> EarningsPeriodItem:
    return EarningsPeriodItem(
        id=row.id,
        starts_on=row.starts_on,
        ends_on=row.ends_on,
        pay_frequency=row.pay_frequency,
        finalized_on=row.finalized_at.astimezone(zone).date(),
        total=row.total,
        paid_total=row.paid_total,
        paid_state=payroll.paid_state(row.total, row.paid_total),
        last_paid_on=row.last_paid_on,
        line_count=row.line_count,
        payslip=_reference(row),
    )


async def _zone(session: AsyncSession) -> ZoneInfo:
    return ZoneInfo(await administration.get_center_time_zone(session))


async def list_earnings(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    *,
    page: OffsetParams,
) -> OffsetPage[EarningsPeriodItem]:
    """``GET …/tutors/{id}/earnings`` (C2). A fixed number of statements,
    whatever the page size."""
    await _visible_tutor(session, principal, tutor_user_id)
    await _known_tutor(session, tutor_user_id)
    rows = await payroll.list_finalized_periods(session, tutor_user_id, page)
    zone = await _zone(session)
    return OffsetPage[EarningsPeriodItem](
        items=[_item(row, zone) for row in rows.items],
        total=rows.total,
        limit=rows.limit,
        offset=rows.offset,
    )


async def read_earnings(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    period_id: uuid.UUID,
) -> EarningsPeriodDetail:
    """``GET …/tutors/{id}/earnings/{period_id}`` (C3)."""
    await _visible_tutor(session, principal, tutor_user_id)
    row = await payroll.finalized_period(session, tutor_user_id, period_id)
    if row is None:
        raise RecordNotFoundError()
    lines = await payroll.earnings_lines(session, period_id)
    payments = await payroll.list_payments(session, period_id)
    zone = await _zone(session)
    return EarningsPeriodDetail(
        period=_item(row, zone),
        lines=lines,
        summary=payroll.summarize(lines),
        total=row.total,
        payments=[
            EarningsPayment(
                id=payment.id,
                paid_on=payment.paid_on,
                amount=payment.amount,
                method_code=payment.method_code,
                reference=payment.reference,
                reversed_on=(
                    None
                    if payment.reversed_at is None
                    else payment.reversed_at.astimezone(zone).date()
                ),
            )
            for payment in payments
        ],
        payslip=_reference(row),
    )


async def load_payslip(
    session: AsyncSession,
    principal: Principal,
    tutor_user_id: uuid.UUID,
    payslip_id: uuid.UUID,
) -> payroll.StoredPayslip:
    """The tutor's payslip under the record policy, or ``404`` (C4)."""
    await _visible_tutor(session, principal, tutor_user_id)
    stored = await payroll.get_payslip(session, tutor_user_id, payslip_id)
    if stored is None:
        raise RecordNotFoundError()
    return stored


def render(stored: payroll.StoredPayslip) -> PayslipFile:
    """The payslip's PDF in its stored language and template version
    (synchronous; see the module docstring)."""
    try:
        data = payroll.render_payslip(stored)
    except Exception as error:
        # The payslip id and the error type only: never the snapshot.
        logger.error(  # noqa: TRY400 - no traceback: it may quote the data
            "payslip %s could not be rendered (%s)", stored.id, type(error).__name__
        )
        raise PayslipRenderError() from None
    return PayslipFile(filename=f"{stored.number}.pdf", data=data)
