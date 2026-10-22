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
  order). For a **series** offer (#233 E2) the series must also have a
  tutor again; the series row is locked before the sessions.
- A **substitution** offer's session stays ``confirmed`` (#55 D2), so
  "Mark resolved" is always allowed for it.
- **Reject ("Dismiss")**: closes the item; the session is unchanged
  (cancelling sessions belongs to #55 / #58).
- **Auto-close**: offering the session again cancels the item
  (``offer_flow``); for a series offer, offering the series again
  (``series_offers``).

``summaries``: ``{class_session_id, subject_name, starts_at, tutor_name,
outcome (declined | expired), decline_note, session_state, class_series_id,
from_date, occurrence_count, kind, original_tutor_name}`` by offer id, in
four statements whatever the page size. ``kind`` is ``assignment`` or
``substitution`` (#55 D2); ``original_tutor_name`` is the tutor a
substitution would have replaced (``null`` for an assignment): the session
is still taught by them. The three series keys are ``null`` for a session offer; for a
series offer without a covered session, ``class_session_id``,
``starts_at`` and ``session_state`` are ``null``.

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
    ClassSeries,
    ClassSession,
    ClassSessionSubstitution,
)
from app.modules.scheduling.offer_flow import (
    REASSIGNMENT_TYPE,
    covered_session_ids,
    lock_series,
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
        series_id = await session.scalar(
            select(AssignmentOffer.class_series_id).where(
                AssignmentOffer.id == request.subject_id
            )
        )
        series = None if series_id is None else await lock_series(session, series_id)
        sessions = await lock_sessions(
            session, await covered_session_ids(session, request.subject_id)
        )
        if any(row.state == SessionState.UNASSIGNED for row in sessions):
            raise ReassignmentStillNeededError()
        if series is not None and series.tutor_user_id is None:
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
        found = (
            await session.execute(
                select(
                    AssignmentOffer,
                    ClassSeries.subject_id,
                    ClassSessionSubstitution.original_tutor_user_id,
                )
                .outerjoin(
                    ClassSeries, ClassSeries.id == AssignmentOffer.class_series_id
                )
                .outerjoin(
                    ClassSessionSubstitution,
                    ClassSessionSubstitution.offer_id == AssignmentOffer.id,
                )
                .where(AssignmentOffer.id.in_(ids))
            )
        ).all()
        offers = {offer.id: offer for offer, _, _ in found}
        series_subjects = {offer.id: subject for offer, subject, _ in found if subject}
        originals = {offer.id: original for offer, _, original in found if original}
        first: dict[uuid.UUID, ClassSession] = {}
        covered: dict[uuid.UUID, int] = {}
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
            covered[offer_id] = covered.get(offer_id, 0) + 1
        subjects = await catalog.subject_references(
            session,
            {row.subject_id for row in first.values()} | set(series_subjects.values()),
        )
        accounts = await identity.get_account_references(
            session,
            {offer.tutor_user_id for offer in offers.values()}
            | set(originals.values()),
        )
        summaries: dict[uuid.UUID, Mapping[str, Any]] = {}
        for offer_id, offer in offers.items():
            earliest = first.get(offer_id)
            account = accounts.get(offer.tutor_user_id)
            subject_id = (
                earliest.subject_id
                if earliest is not None
                else series_subjects.get(offer_id)
            )
            if subject_id is None or account is None:  # pragma: no cover
                continue
            subject = subjects.get(subject_id)
            summaries[offer_id] = {
                "class_session_id": None if earliest is None else str(earliest.id),
                "subject_name": "" if subject is None else subject.name,
                "starts_at": None if earliest is None else rfc3339(earliest.starts_at),
                "tutor_name": account.display_name,
                "outcome": offer.status,
                "decline_note": offer.decline_note,
                "session_state": None if earliest is None else earliest.state,
                "class_series_id": None
                if offer.class_series_id is None
                else str(offer.class_series_id),
                "from_date": None
                if offer.from_date is None
                else offer.from_date.isoformat(),
                "occurrence_count": None
                if offer.class_series_id is None
                else covered.get(offer_id, 0),
                "kind": offer.kind,
                "original_tutor_name": _name(accounts, originals.get(offer_id)),
            }
        return summaries


def _name(accounts: Mapping[uuid.UUID, Any], user_id: uuid.UUID | None) -> str | None:
    account = None if user_id is None else accounts.get(user_id)
    return None if account is None else str(account.display_name)


OFFER_REASSIGNMENT_HANDLER: Final = OfferReassignmentHandler()
