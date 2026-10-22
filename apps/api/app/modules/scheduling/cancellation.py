"""Cancelling one session or occurrence (#55 C; spec §3 "cancellations",
W1.5; ADR-0005 D2, D9).

**Order** (:func:`cancel_class_session`; the route ``POST
/api/v1/class-sessions/{id}/cancel`` adds ``401``/``403``
(``schedule.manage``) and the body's ``422`` before it):

1. the session, unlocked → ``404 not_found``;
2. the locks (:mod:`~app.modules.scheduling.substitutions` G1): the series
   row ``FOR UPDATE`` when the row belongs to one (the #233 B3 step 3
   position, so the nightly job and the series-offer answers serialize
   with the cancel), then the session ``FOR UPDATE``, then the offers;
3. ``409 version_conflict``;
4. ``409 class_session_not_cancellable``: ``details.reason = state`` (with
   ``details.state``) for ``completed``, ``cancelled`` and
   ``skipped_closure``; ``started`` from ``starts_at`` on;
5. the recipients, from the state **before** the write;
6. ``withdraw_pending_offers``: every pending session-scope offer of either
   kind is ``withdrawn``. A row covered by a pending **series** offer stays
   covered; the series offer is untouched (#233 B6);
7. ``→ cancelled`` with the trimmed reason (``cancelled_at`` = now);
8. the inbox: the open ``scheduling.offer_reassignment`` items of the
   session's session-scope offers and the open
   ``scheduling.occurrence_conflict`` item of a ``conflict`` row are
   cancelled by the caller (items of series offers stay: they cover other
   rows);
9. the named audit event ``scheduling.session_cancelled`` ``{class_session_id,
   series_id, previous_state, reason, withdrawn_offer_ids,
   cancelled_request_ids}``, on top of the automatic update event;
10. ``scheduling.session_cancelled`` (no reason in the params) to the tutor
    of an ``offered`` or ``confirmed`` session, the substitute of a
    pending substitution, and for ``confirmed`` only the students' logins
    and their guardians; never the actor;
11. the cancellation steps registered by the composition root
    (:func:`register_cancellation_step`; ``app/workflows/
    session_cancellation.py``, the E3 seam for billing M6-14 and pay
    M7-04), in the same transaction. No policy classification is stored
    for a center cancellation (#55 E3).

A cancelled occurrence keeps ``series_id`` and ``original_start``, so the
nightly materialization never re-creates its date (``UNIQUE(series_id,
original_start)``, #53); the series itself is unchanged. The group row lock
is not taken: a cancel changes no participant and no room.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import record_audit_event
from app.core.authz import RecordNotFoundError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.scheduling.errors import ClassSessionNotCancellableError
from app.modules.scheduling.materialize import OCCURRENCE_CONFLICT_TYPE
from app.modules.scheduling.models import (
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_SCOPE_SESSION,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
)
from app.modules.scheduling.offer_flow import (
    close_reassignment_item_of,
    lock_series,
    lock_sessions,
    withdraw_pending_offers,
)
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.substitution_records import (
    SESSION_CANCELLED,
    family_recipients,
    notify_session,
    pending_substitution_of,
)
from app.modules.scheduling.substitutions import cancel_audience, cancel_refusal

CANCELLED_EVENT: Final = "scheduling.session_cancelled"

CancellationInitiator = Literal["center", "family", "tutor"]


class CancellationStep(Protocol):
    """A step run in the cancel's transaction, after the transition (#55
    C5): ``app/workflows/session_cancellation.on_session_cancelled``."""

    def __call__(
        self,
        session: AsyncSession,
        *,
        class_session_id: uuid.UUID,
        initiated_by: CancellationInitiator,
        cancelled_at: datetime,
        by_user_id: uuid.UUID | None,
    ) -> Awaitable[None]: ...


_steps: list[CancellationStep] = []


def register_cancellation_step(step: CancellationStep) -> None:
    """Register a step run by every cancel (again: a no-op). ``scheduling``
    never imports ``app.workflows`` (ADR-0002); ``app.wiring`` registers
    the workflow here."""
    if step not in _steps:
        _steps.append(step)


def registered_cancellation_steps() -> tuple[Callable[..., Awaitable[None]], ...]:
    """The registered steps (tests)."""
    return tuple(_steps)


async def _cancel_open_items(
    session: AsyncSession,
    row: ClassSession,
    *,
    previous_state: str,
    by_user_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """Step 8: the ids of the inbox items cancelled."""
    cancelled: list[uuid.UUID] = []
    offer_ids = await session.scalars(
        select(AssignmentOffer.id)
        .join(
            AssignmentOfferSession,
            AssignmentOfferSession.offer_id == AssignmentOffer.id,
        )
        .where(AssignmentOfferSession.class_session_id == row.id)
        .where(AssignmentOffer.scope == OFFER_SCOPE_SESSION)
        .where(AssignmentOffer.status.in_((OFFER_DECLINED, OFFER_EXPIRED)))
        .order_by(AssignmentOffer.id)
    )
    for offer_id in list(offer_ids):
        request_id = await close_reassignment_item_of(
            session, offer_id, by_user_id=by_user_id
        )
        if request_id is not None:
            cancelled.append(request_id)
    if previous_state == SessionState.CONFLICT:
        request_id = await administration.approval_request_id_for_subject(
            session, request_type=OCCURRENCE_CONFLICT_TYPE, subject_id=row.id
        )
        if request_id is not None:
            try:
                await administration.cancel_approval_request(
                    session, request_id=request_id, by_user_id=by_user_id
                )
                cancelled.append(request_id)
            except administration.ApprovalTransitionInvalidError:
                # Already decided: nothing is open. Raised before any write.
                pass
    return cancelled


async def cancel_class_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    *,
    expected_version: int,
    reason: str,
    by_user_id: uuid.UUID | None,
    initiated_by: CancellationInitiator = "center",
) -> ClassSession:
    """Cancel one session (C3; see the module docstring for the order) and
    return the row. ``reason`` is the trimmed reason. #62 (M4-17) calls it
    with ``initiated_by = "family"`` or ``"tutor"`` and stores its own
    classification."""
    found = (
        await session.execute(
            select(ClassSession.series_id).where(ClassSession.id == class_session_id)
        )
    ).first()
    if found is None:
        raise RecordNotFoundError()
    series_id = found[0]
    if series_id is not None:
        await lock_series(session, series_id)
    [row] = await lock_sessions(session, [class_session_id])
    ensure_version(row, expected_version)
    now = clock.now()
    refusal = cancel_refusal(row.state, row.starts_at, now)
    if refusal == "state":
        raise ClassSessionNotCancellableError(refusal, state=row.state)
    if refusal is not None:
        raise ClassSessionNotCancellableError(refusal)

    previous_state = row.state
    audience = cancel_audience(previous_state)
    recipients: set[uuid.UUID] = set()
    if "tutor" in audience and row.tutor_user_id is not None:
        recipients.add(row.tutor_user_id)
    pending = await pending_substitution_of(session, row.id)
    if pending is not None:
        recipients.add(pending.substitute_tutor_user_id)
    if "family" in audience:
        recipients |= await family_recipients(session, row.id)

    withdrawn = await withdraw_pending_offers(session, [row.id], by_user_id=by_user_id)
    await transition_class_session(
        session,
        row.id,
        to_state=SessionState.CANCELLED,
        expected_version=row.version,
        reason=reason,
    )
    cancelled_requests = await _cancel_open_items(
        session, row, previous_state=previous_state, by_user_id=by_user_id
    )
    record_audit_event(
        session,
        action=CANCELLED_EVENT,
        record_type="class_sessions",
        record_id=row.id,
        details={
            "class_session_id": row.id,
            "series_id": row.series_id,
            "previous_state": previous_state,
            "reason": row.cancellation_reason,
            "withdrawn_offer_ids": withdrawn,
            "cancelled_request_ids": cancelled_requests,
        },
    )
    await notify_session(
        session,
        recipients,
        SESSION_CANCELLED,
        class_session_id=row.id,
        starts_at=row.starts_at,
        actor_id=by_user_id,
    )
    cancelled_at = row.cancelled_at
    assert cancelled_at is not None  # noqa: S101 - just cancelled
    for step in _steps:
        await step(
            session,
            class_session_id=row.id,
            initiated_by=initiated_by,
            cancelled_at=cancelled_at,
            by_user_id=by_user_id,
        )
    await session.flush()
    return row
