"""The ``payroll.period`` approval type (#110 G; the #78/#312 pattern; ADR-0017
E10): a prepared period waits in the approval inbox, and ``payroll.finalize``
holders decide it on its own review page.

**Decisions go through the period routes.** ``finalize`` moves the period first
and then resolves the item through ``administration.service.decide_approval_request``
in the same Unit of Work (lock order: the period, then the request), and a
``return`` cancels the item. So ``approve`` accepts only a period already
``finalized``; ``reject`` always refuses, and ``request_changes`` isn't supported:
``return`` is the way back, and it needs no reauthentication.

**Generic inbox routes** (``inbox_decisions = False``, #312
``inbox_route_error``): ``409 payroll_review_route_required`` with
``details.review_path`` for every request status, right after the core's ``404``
check. The period is read without a lock, and nothing changes.

**Summaries:** ``{period_id, tutor {id, display_name}, starts_on, ends_on,
prepared_by {display_name}, prepared_at}`` in two statements. No amount and no
reason: the inbox lists it to everyone who may finalize.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.payroll import service as payroll

PAYROLL_PERIOD_TYPE: Final = "payroll.period"
PAYROLL_FINALIZE: Final = "payroll.finalize"
FINALIZED: Final = "finalized"


def review_path(period_id: uuid.UUID) -> str:
    """The review page of a period (the page is M7-07b's)."""
    return f"/payroll/periods/{period_id}"


class PayrollPeriodApprovalHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of payroll
    periods; the subject is the period (see the module docstring)."""

    request_type: Final = PAYROLL_PERIOD_TYPE
    review_permission: Final = PAYROLL_FINALIZE
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True
    inbox_decisions: Final = False
    """The inbox lists no decisions, only the "Review" link."""

    async def inbox_route_error(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
    ) -> payroll.PayrollReviewRouteRequiredError:
        """#312: the generic routes' answer for any request status."""
        return payroll.PayrollReviewRouteRequiredError(review_path(request.subject_id))

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        found = (await payroll.period_rows(session, [request.subject_id])).get(
            request.subject_id
        )
        if found is None or found.status != FINALIZED:
            raise payroll.PayrollReviewRouteRequiredError(
                review_path(request.subject_id)
            )

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        raise payroll.PayrollReviewRouteRequiredError(review_path(request.subject_id))

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        raise payroll.PayrollReviewRouteRequiredError(review_path(request.subject_id))

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """By period id, in two statements (the periods, the names)."""
        periods = await payroll.period_rows(session, set(subject_ids))
        people = await identity.get_account_references(
            session,
            {
                user_id
                for period in periods.values()
                for user_id in (period.tutor_user_id, period.prepared_by_user_id)
                if user_id is not None
            },
        )

        def name(user_id: uuid.UUID | None) -> str:
            person = None if user_id is None else people.get(user_id)
            return "" if person is None else person.display_name

        return {
            period.id: {
                "period_id": str(period.id),
                "tutor": {
                    "id": str(period.tutor_user_id),
                    "display_name": name(period.tutor_user_id),
                },
                "starts_on": period.starts_on.isoformat(),
                "ends_on": period.ends_on.isoformat(),
                "prepared_by": {"display_name": name(period.prepared_by_user_id)},
                "prepared_at": (
                    None
                    if period.prepared_at is None
                    else period.prepared_at.isoformat()
                ),
            }
            for period in periods.values()
        }


PAYROLL_PERIOD_HANDLER: Final = PayrollPeriodApprovalHandler()
"""Registered by ``app.wiring``."""
