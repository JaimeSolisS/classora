"""Storing offers and their notifications (#52 A, C1, F).

Used by the create path (:mod:`~app.modules.scheduling.booking`, #48 C1)
and by the later offer, answer and expiry paths
(:mod:`~app.modules.scheduling.offer_flow`). Every function runs in the
caller's Unit of Work, after the caller has locked the covered sessions
(the lock order of :mod:`~app.modules.scheduling.offers`), and never
commits.

**Notifications (F).** Registered by ``app.wiring`` with
:func:`register_offer_notifications`:

- ``scheduling.offer_received`` to the offered tutor: ``{offer_id,
  subject_name, starts_at, respond_by}``;
- ``scheduling.offer_declined`` / ``scheduling.offer_expired`` to every
  active holder of ``schedule.assign`` (``identity.service.
  active_user_ids_with_permission``): ``{offer_id, class_session_id,
  subject_name, starts_at, tutor_name}``. The decline note is never a param
  (the #44 rule); staff read it in the offer history and the inbox.

Instants are RFC 3339 strings in UTC; the client formats them in the center
zone.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.scheduling.models import (
    OFFER_PENDING,
    AssignmentOffer,
    AssignmentOfferSession,
)

OFFER_RECEIVED: Final = "scheduling.offer_received"
OFFER_DECLINED: Final = "scheduling.offer_declined"
OFFER_EXPIRED: Final = "scheduling.offer_expired"
SCHEDULE_ASSIGN: Final = "schedule.assign"
"""The identity catalog's assignment permission (repeated here: ADR-0002)."""
SESSION_SCOPE: Final = "session"


class OfferReceivedParams(BaseModel):
    """The params of ``scheduling.offer_received``."""

    offer_id: str
    subject_name: str
    starts_at: str
    respond_by: str


class OfferClosedParams(BaseModel):
    """The params of ``scheduling.offer_declined`` / ``_expired`` (never the
    decline note)."""

    offer_id: str
    class_session_id: str
    subject_name: str
    starts_at: str
    tutor_name: str


def register_offer_notifications() -> None:
    """Register the three offer notification types (again: a no-op)."""
    administration.register_notification_type(OFFER_RECEIVED, OfferReceivedParams)
    for code in (OFFER_DECLINED, OFFER_EXPIRED):
        administration.register_notification_type(code, OfferClosedParams)


def rfc3339(value: datetime) -> str:
    """An instant as an RFC 3339 string in UTC (``Z``)."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


async def insert_offer(
    session: AsyncSession,
    *,
    tutor_user_id: uuid.UUID,
    offered_by_user_id: uuid.UUID,
    offered_at: datetime,
    respond_by: datetime,
    class_session_ids: Iterable[uuid.UUID],
) -> AssignmentOffer:
    """A new ``pending`` session-scope offer and its covered-session rows,
    flushed (audited ``create`` events)."""
    offer = AssignmentOffer(
        id=uuid.uuid7(),
        scope=SESSION_SCOPE,
        tutor_user_id=tutor_user_id,
        status=OFFER_PENDING,
        offered_at=offered_at,
        offered_by_user_id=offered_by_user_id,
        respond_by=respond_by,
    )
    session.add(offer)
    await session.flush()
    session.add_all(
        AssignmentOfferSession(offer_id=offer.id, class_session_id=session_id)
        for session_id in sorted(set(class_session_ids))
    )
    await session.flush()
    return offer


async def notify_offer_received(
    session: AsyncSession,
    offer: AssignmentOffer,
    *,
    subject_name: str,
    starts_at: datetime,
) -> None:
    """F1: tell the offered tutor (in the caller's transaction)."""
    await administration.create_notification(
        session,
        recipient_user_id=offer.tutor_user_id,
        notification_type=OFFER_RECEIVED,
        params=OfferReceivedParams(
            offer_id=str(offer.id),
            subject_name=subject_name,
            starts_at=rfc3339(starts_at),
            respond_by=rfc3339(offer.respond_by),
        ),
    )


async def notify_offer_closed(
    session: AsyncSession,
    offer: AssignmentOffer,
    *,
    notification_type: str,
    class_session_id: uuid.UUID,
    subject_name: str,
    starts_at: datetime,
    tutor_name: str,
) -> None:
    """F2: tell every active holder of ``schedule.assign`` that the offer was
    declined or expired."""
    params = OfferClosedParams(
        offer_id=str(offer.id),
        class_session_id=str(class_session_id),
        subject_name=subject_name,
        starts_at=rfc3339(starts_at),
        tutor_name=tutor_name,
    )
    for user_id in await identity.active_user_ids_with_permission(
        session, SCHEDULE_ASSIGN
    ):
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=notification_type,
            params=params,
        )
