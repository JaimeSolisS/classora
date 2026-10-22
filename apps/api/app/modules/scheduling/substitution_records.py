"""Storing and reading substitutions, and the #55 notification types (#55
A2, E1, F).

Used by :mod:`~app.modules.scheduling.substitution_flow` (substitute,
withdraw, list), by :mod:`~app.modules.scheduling.offer_flow` (the
substitute's accept) and by :mod:`~app.modules.scheduling.cancellation`.
Every function runs in the caller's Unit of Work and never commits.

**Notifications (F2-F4)**, registered by ``app.wiring`` with
:func:`register_substitution_notifications`. Their params are
``{class_session_id, starts_at}`` only: never the reason, never a tutor's
name (#67 decides what families see):

- ``scheduling.tutor_substituted``: to the original tutor, when the
  substitute accepted;
- ``scheduling.session_tutor_changed``: to the student's own login and the
  linked guardians (``families.service.student_notification_recipients``),
  when the substitute accepted;
- ``scheduling.session_cancelled``: a session cancelled by the center (the
  recipients are :mod:`~app.modules.scheduling.cancellation`'s).

The actor is never notified.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Final

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.administration import service as administration
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.models import (
    OFFER_KIND_SUBSTITUTION,
    OFFER_PENDING,
    AssignmentOffer,
    ClassSessionParticipant,
    ClassSessionSubstitution,
)
from app.modules.scheduling.offer_records import rfc3339
from app.modules.scheduling.schemas import OfferPerson, SubstitutionItem

TUTOR_SUBSTITUTED: Final = "scheduling.tutor_substituted"
SESSION_TUTOR_CHANGED: Final = "scheduling.session_tutor_changed"
SESSION_CANCELLED: Final = "scheduling.session_cancelled"


class SessionNotificationParams(BaseModel):
    """The params of the three #55 types: the session and its start."""

    class_session_id: str
    starts_at: str


def register_substitution_notifications() -> None:
    """Register the three types (again: a no-op)."""
    for code in (TUTOR_SUBSTITUTED, SESSION_TUTOR_CHANGED, SESSION_CANCELLED):
        administration.register_notification_type(code, SessionNotificationParams)


async def family_recipients(
    session: AsyncSession, class_session_id: uuid.UUID
) -> set[uuid.UUID]:
    """The student logins and linked guardians of every participant of the
    session (two statements)."""
    student_ids = set(
        await session.scalars(
            select(ClassSessionParticipant.student_id).where(
                ClassSessionParticipant.class_session_id == class_session_id
            )
        )
    )
    found = await families.student_notification_recipients(session, student_ids)
    return {user_id for users in found.values() for user_id in users}


async def notify_session(
    session: AsyncSession,
    user_ids: Iterable[uuid.UUID],
    notification_type: str,
    *,
    class_session_id: uuid.UUID,
    starts_at: datetime,
    actor_id: uuid.UUID | None,
) -> None:
    """One notification of ``notification_type`` per recipient (sorted,
    de-duplicated), never to the actor."""
    params = SessionNotificationParams(
        class_session_id=str(class_session_id), starts_at=rfc3339(starts_at)
    )
    for user_id in sorted(set(user_ids) - {actor_id}):
        await administration.create_notification(
            session,
            recipient_user_id=user_id,
            notification_type=notification_type,
            params=params,
        )


async def substitution_of_offer(
    session: AsyncSession, offer_id: uuid.UUID
) -> ClassSessionSubstitution | None:
    """The substitution row of a ``substitution`` offer (unlocked; the rows
    never change)."""
    found: ClassSessionSubstitution | None = await session.scalar(
        select(ClassSessionSubstitution).where(
            ClassSessionSubstitution.offer_id == offer_id
        )
    )
    return found


async def pending_substitution_of(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSessionSubstitution | None:
    """The session's pending substitution (at most one, A3), or ``None``."""
    found: ClassSessionSubstitution | None = await session.scalar(
        select(ClassSessionSubstitution)
        .join(AssignmentOffer, AssignmentOffer.id == ClassSessionSubstitution.offer_id)
        .where(
            ClassSessionSubstitution.class_session_id == class_session_id,
            AssignmentOffer.kind == OFFER_KIND_SUBSTITUTION,
            AssignmentOffer.status == OFFER_PENDING,
        )
    )
    return found


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


async def substitution_items(
    session: AsyncSession,
    rows: Sequence[tuple[ClassSessionSubstitution, AssignmentOffer]],
) -> list[SubstitutionItem]:
    """The items of ``rows`` (a substitution with its offer), in their
    order: one statement for the accounts."""
    if not rows:
        return []
    accounts = await identity.get_account_references(
        session,
        {
            user_id
            for substitution, _ in rows
            for user_id in (
                substitution.original_tutor_user_id,
                substitution.substitute_tutor_user_id,
                substitution.requested_by_user_id,
            )
        },
    )

    def person(user_id: uuid.UUID) -> OfferPerson:
        account = accounts[user_id]
        return OfferPerson(id=account.id, display_name=account.display_name)

    return [
        SubstitutionItem(
            id=substitution.id,
            class_session_id=substitution.class_session_id,
            status=offer.status,  # type: ignore[arg-type]
            offer_id=offer.id,
            original_tutor=person(substitution.original_tutor_user_id),
            substitute_tutor=person(substitution.substitute_tutor_user_id),
            reason=substitution.reason,
            requested_by=person(substitution.requested_by_user_id),
            requested_at=_utc(offer.offered_at),
            respond_by=_utc(offer.respond_by),
            responded_at=None
            if offer.responded_at is None
            else _utc(offer.responded_at),
            decline_note=offer.decline_note,
        )
        for substitution, offer in rows
    ]
