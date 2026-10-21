"""The ``scheduling.offer_reassignment`` approval type (#52 E1, E2; ADR-0005
D3: a declined or expired offer becomes an actionable inbox item).

One item per declined or expired offer (the subject is the **offer**, so a
session declined twice gets two items), created by
:mod:`~app.modules.scheduling.offer_flow` with no requester. Reviewers hold
``schedule.assign`` (Owners, Administrators and Academic Coordinators); items
can't be sent back for changes, and dismissing one needs a reason.

- **Approve ("Mark resolved")**: allowed only once no covered session is
  still ``unassigned`` (``409 reassignment_still_needed`` otherwise; the
  item stays pending). The sessions are locked first (the offer lock
  order).
- **Reject ("Dismiss")**: closes the item; the session is unchanged
  (cancelling sessions belongs to #55 / #58).
- **Auto-close**: offering the session again cancels the item
  (``offer_flow``).

``summaries``: ``{class_session_id, subject_name, starts_at, tutor_name,
outcome (declined | expired), decline_note, session_state}`` by offer id,
in four statements whatever the page size.

Exported by ``scheduling.service``; ``app.wiring`` registers it.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling.errors import ReassignmentStillNeededError
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
)
from app.modules.scheduling.offer_flow import (
    REASSIGNMENT_TYPE,
    covered_session_ids,
    lock_sessions,
)
from app.modules.scheduling.offer_records import SCHEDULE_ASSIGN, rfc3339
from app.modules.scheduling.states import SessionState


class OfferReassignmentHandler:
    """The :class:`~app.modules.administration.service.ApprovalHandler` of
    reassignment items; the subject is the declined or expired offer."""

    request_type: Final = REASSIGNMENT_TYPE
    review_permission: Final = SCHEDULE_ASSIGN
    supports_request_changes: Final = False
    reason_required_on_reject: Final = True

    async def approve(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
    ) -> None:
        sessions = await lock_sessions(
            session, await covered_session_ids(session, request.subject_id)
        )
        if any(row.state == SessionState.UNASSIGNED for row in sessions):
            raise ReassignmentStillNeededError()

    async def reject(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str | None,
    ) -> None:
        # Dismiss: the core stores the (required) reason; the session is
        # left as it is.
        return None

    async def request_changes(
        self,
        session: AsyncSession,
        request: administration.ApprovalRequestView,
        principal: Principal,
        reason: str,
    ) -> None:
        # Never called: supports_request_changes is False.
        raise NotImplementedError

    async def summaries(
        self, session: AsyncSession, subject_ids: Iterable[uuid.UUID]
    ) -> Mapping[uuid.UUID, Mapping[str, Any]]:
        """See the module docstring; four statements."""
        ids = set(subject_ids)
        if not ids:
            return {}
        offers = {
            offer.id: offer
            for offer in await session.scalars(
                select(AssignmentOffer).where(AssignmentOffer.id.in_(ids))
            )
        }
        first: dict[uuid.UUID, ClassSession] = {}
        for offer_id, row in (
            await session.execute(
                select(AssignmentOfferSession.offer_id, ClassSession)
                .join(
                    ClassSession,
                    ClassSession.id == AssignmentOfferSession.class_session_id,
                )
                .where(AssignmentOfferSession.offer_id.in_(offers))
                .order_by(ClassSession.starts_at.desc(), ClassSession.id.desc())
            )
        ).all():
            first[offer_id] = row  # the earliest wins (descending order)
        subjects = await catalog.subject_references(
            session, {row.subject_id for row in first.values()}
        )
        accounts = await identity.get_account_references(
            session, {offer.tutor_user_id for offer in offers.values()}
        )
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for offer_id, offer in offers.items():
            earliest = first.get(offer_id)
            account = accounts.get(offer.tutor_user_id)
            if earliest is None or account is None:  # pragma: no cover
                continue
            subject = subjects.get(earliest.subject_id)
            summaries[offer_id] = {
                "class_session_id": str(earliest.id),
                "subject_name": "" if subject is None else subject.name,
                "starts_at": rfc3339(earliest.starts_at),
                "tutor_name": account.display_name,
                "outcome": offer.status,
                "decline_note": offer.decline_note,
                "session_state": earliest.state,
            }
        return summaries


OFFER_REASSIGNMENT_HANDLER: Final = OfferReassignmentHandler()
