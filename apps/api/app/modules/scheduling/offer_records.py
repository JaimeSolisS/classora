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

A **series** offer (#233 E1) adds ``class_series_id``, ``from_date`` (the
local date) and ``occurrence_count`` (the covered sessions at the time of
the event) to the same three types; a session offer's params don't carry
these keys. For a series, ``starts_at`` is the first covered start (or the
rule's next occurrence when none is covered yet), and ``class_session_id``
of a declined or expired series offer is the first covered session, or
``null`` when none was covered.

**Series offers** (#233 B2): :func:`attach_to_pending_series_offer` is called
by every writer that creates ``offered`` rows of a series (materialization in
extend mode and the ``scheduling.occurrence_conflict`` approve), so the
invariant of :mod:`~app.modules.scheduling.offers` holds for series rows.

Instants are RFC 3339 strings in UTC; the client formats them in the center
zone.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any, Final

from pydantic import BaseModel, SerializerFunctionWrapHandler, model_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.identity import service as identity
from app.modules.scheduling.models import (
    OFFER_PENDING,
    OFFER_SCOPE_SERIES,
    OFFER_SCOPE_SESSION,
    SERIES_ASSIGNMENT_PENDING,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSession,
)

OFFER_RECEIVED: Final = "scheduling.offer_received"
OFFER_DECLINED: Final = "scheduling.offer_declined"
OFFER_EXPIRED: Final = "scheduling.offer_expired"
SCHEDULE_ASSIGN: Final = "schedule.assign"
"""The identity catalog's assignment permission (repeated here: ADR-0002)."""
SESSION_SCOPE: Final = OFFER_SCOPE_SESSION
SERIES_SCOPE: Final = OFFER_SCOPE_SERIES
SERIES_PARAMS: Final = ("class_series_id", "from_date", "occurrence_count")
"""The series keys of the offer params (#233 E1); absent for a session
offer."""


class _OfferParams(BaseModel):
    """The series keys, left out of the stored params when unset."""

    class_series_id: str | None = None
    from_date: str | None = None
    occurrence_count: int | None = None

    @model_serializer(mode="wrap")
    def _without_unset_series_keys(
        self, handler: SerializerFunctionWrapHandler
    ) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        return {
            key: value
            for key, value in data.items()
            if key not in SERIES_PARAMS or value is not None
        }


class OfferReceivedParams(_OfferParams):
    """The params of ``scheduling.offer_received``."""

    offer_id: str
    subject_name: str
    starts_at: str
    respond_by: str


class OfferClosedParams(_OfferParams):
    """The params of ``scheduling.offer_declined`` / ``_expired`` (never the
    decline note)."""

    offer_id: str
    class_session_id: str | None
    subject_name: str
    starts_at: str
    tutor_name: str


def series_params(
    offer: AssignmentOffer, occurrence_count: int
) -> dict[str, str | int | None]:
    """The series keys of ``offer``'s params (empty for a session offer)."""
    if offer.scope != SERIES_SCOPE or offer.from_date is None:
        return {}
    return {
        "class_series_id": str(offer.class_series_id),
        "from_date": offer.from_date.isoformat(),
        "occurrence_count": occurrence_count,
    }


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
    class_series_id: uuid.UUID | None = None,
    from_date: date | None = None,
) -> AssignmentOffer:
    """A new ``pending`` offer and its covered-session rows, flushed
    (audited ``create`` events): session scope, or series scope with
    ``class_series_id`` and ``from_date`` (#233)."""
    offer = AssignmentOffer(
        id=uuid.uuid7(),
        scope=SESSION_SCOPE if class_series_id is None else SERIES_SCOPE,
        class_series_id=class_series_id,
        from_date=from_date,
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
    occurrence_count: int = 1,
) -> None:
    """F1: tell the offered tutor (in the caller's transaction)."""
    await administration.create_notification(
        session,
        recipient_user_id=offer.tutor_user_id,
        notification_type=OFFER_RECEIVED,
        params=OfferReceivedParams.model_validate(
            {
                "offer_id": str(offer.id),
                "subject_name": subject_name,
                "starts_at": rfc3339(starts_at),
                "respond_by": rfc3339(offer.respond_by),
                **series_params(offer, occurrence_count),
            }
        ),
    )


async def notify_offer_closed(
    session: AsyncSession,
    offer: AssignmentOffer,
    *,
    notification_type: str,
    class_session_id: uuid.UUID | None,
    subject_name: str,
    starts_at: datetime,
    tutor_name: str,
    occurrence_count: int = 1,
) -> None:
    """F2: tell every active holder of ``schedule.assign`` that the offer was
    declined or expired."""
    params = OfferClosedParams.model_validate(
        {
            "offer_id": str(offer.id),
            "class_session_id": None
            if class_session_id is None
            else str(class_session_id),
            "subject_name": subject_name,
            "starts_at": rfc3339(starts_at),
            "tutor_name": tutor_name,
            **series_params(offer, occurrence_count),
        }
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


async def pending_series_offer(
    session: AsyncSession, class_series_id: uuid.UUID
) -> AssignmentOffer | None:
    """The series' pending series-scope offer (at most one), unlocked."""
    found: AssignmentOffer | None = await session.scalar(
        select(AssignmentOffer).where(
            AssignmentOffer.class_series_id == class_series_id,
            AssignmentOffer.status == OFFER_PENDING,
        )
    )
    return found


async def attach_to_pending_series_offer(
    session: AsyncSession, series: ClassSeries, rows: Sequence[ClassSession]
) -> None:
    """#233 B2: the ``offered`` rows a series writer has just created from
    :func:`~app.modules.scheduling.materialize.occurrence_initial_state`
    join the series' pending offer, in the caller's transaction (the caller
    holds the series row ``FOR UPDATE``). The offer's ``respond_by`` is never
    moved: these rows start after its first covered occurrence. Rows of a
    series without a pending assignment are a programming error."""
    if not rows:
        return
    offer = (
        await pending_series_offer(session, series.id)
        if series.assignment_status == SERIES_ASSIGNMENT_PENDING
        else None
    )
    if offer is None or offer.tutor_user_id != series.tutor_user_id:
        raise ValueError(f"series {series.id} has no pending offer to attach to")
    session.add_all(
        AssignmentOfferSession(offer_id=offer.id, class_session_id=row.id)
        for row in sorted(rows, key=lambda row: row.id)
    )
    await session.flush()
