"""Substituting the tutor of one ``confirmed`` session (#55 B, E; spec §3
"substitutes", W1.3-W1.4; ADR-0005 D3 amendment, D9).

The rules, the invariants and the lock order are in
:mod:`~app.modules.scheduling.substitutions`; the substitute's accept,
decline and the expiry are the #52 paths of
:mod:`~app.modules.scheduling.offer_flow`.

**Substitute** (:func:`substitute_class_session`, ``POST
/api/v1/class-sessions/{id}/substitutions``), after the route's
``401``/``403`` (``schedule.assign``; ``schedule.override`` for an
``override``) and the body's ``422`` (``reason_required`` /
``reason_too_long``):

1. the session ``FOR UPDATE`` → ``404 not_found``;
2. ``409 version_conflict``;
3. ``409 class_session_not_substitutable`` (``details.reason`` ``state``
   with ``details.state``, ``started``, or ``substitution_pending`` with
   ``details.substitution_id``);
4. ``lock_tutor`` for the substitute (#63 D5, the #52 C2 position), then
   the zone ``FOR SHARE``;
5. ``422`` in one response: the substitute (``tutor_unknown``,
   ``tutor_inactive``, ``substitute_is_current_tutor``) and ``respond_by``
   (``respond_by_invalid``, the #52 B2 rule with the session's start);
6. the #52 C2 checks for the substitute on the session's own slot, room
   and students (:func:`~app.modules.scheduling.checks.find_offer_conflicts`;
   ``center_closed`` for one-off sessions only, #53);
7. the override decision (#49): ``409 scheduling_conflict`` unless there is
   no conflict, or all are soft and listed; then the override row;
8. the substitute's workload warnings (#50);
9. the ``substitution`` offer (session scope), its covered-session row and
   the substitution row (original tutor, reason, requester, slot
   snapshot). **The session is not changed** (state, tutor, version);
10. ``scheduling.offer_received`` to the substitute (``kind``,
    ``original_tutor_name``; never the reason);
11. the open reassignment items of the session's earlier declined or
    expired substitutions are cancelled by the caller;
12. ``201`` with the substitution and the warnings.

**Withdraw** (:func:`withdraw_substitution`): ``404`` for an unknown
session or a substitution of another session → the session ``FOR UPDATE``
then the offer ``FOR UPDATE`` → ``409 offer_not_pending``
(``details.status``) → the offer ``withdrawn``. The session is unchanged;
no notification and no inbox item.

**Read** (:func:`list_session_substitutions`): newest first, at most four
statements whatever the number of rows.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling.booking import respond_by_error
from app.modules.scheduling.checks import find_offer_conflicts
from app.modules.scheduling.errors import (
    ClassSessionNotSubstitutableError,
    OfferNotPendingError,
    SchedulingConflictError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_tutor
from app.modules.scheduling.models import (
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_KIND_SUBSTITUTION,
    OFFER_PENDING,
    OFFER_WITHDRAWN,
    AssignmentOffer,
    ClassSession,
    ClassSessionSubstitution,
)
from app.modules.scheduling.offer_flow import (
    close_reassignment_item_of,
    lock_sessions,
)
from app.modules.scheduling.offer_records import insert_offer, notify_offer_received
from app.modules.scheduling.offers import RespondByInvalidError, resolve_respond_by
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.schemas import (
    ClassSessionSubstitutions,
    SubstitutionCreate,
    SubstitutionCreated,
    SubstitutionItem,
)
from app.modules.scheduling.substitution_records import (
    pending_substitution_of,
    substitution_items,
)
from app.modules.scheduling.substitutions import substitution_refusal
from app.modules.scheduling.workload_sessions import session_warnings

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"
_SUBSTITUTE_MESSAGES: Final = {
    "tutor_unknown": "Choose an existing tutor.",
    "tutor_inactive": "The tutor's account is not active.",
    "substitute_is_current_tutor": "Choose a tutor other than the session's tutor.",
}


def _substitute_error(code: str) -> FieldError:
    return {
        "path": ["body", "substitute_tutor_user_id"],
        "code": code,
        "message": _SUBSTITUTE_MESSAGES[code],
    }


async def _lock_offer(session: AsyncSession, offer_id: uuid.UUID) -> AssignmentOffer:
    offer = await session.scalar(
        select(AssignmentOffer)
        .where(AssignmentOffer.id == offer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if offer is None:  # pragma: no cover - offers are never deleted
        raise RecordNotFoundError()
    return offer


async def _close_earlier_items(
    session: AsyncSession, class_session_id: uuid.UUID, *, by_user_id: uuid.UUID
) -> None:
    """B2 step 12: cancel the open reassignment items of the session's
    declined or expired substitution offers."""
    offer_ids = await session.scalars(
        select(AssignmentOffer.id)
        .join(
            ClassSessionSubstitution,
            ClassSessionSubstitution.offer_id == AssignmentOffer.id,
        )
        .where(ClassSessionSubstitution.class_session_id == class_session_id)
        .where(AssignmentOffer.status.in_((OFFER_DECLINED, OFFER_EXPIRED)))
        .order_by(AssignmentOffer.id)
    )
    for offer_id in list(offer_ids):
        await close_reassignment_item_of(session, offer_id, by_user_id=by_user_id)


async def substitute_class_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    body: SubstitutionCreate,
    *,
    actor_id: uuid.UUID,
) -> SubstitutionCreated:
    """``POST /api/v1/class-sessions/{id}/substitutions``: see the module
    docstring for the order. The route has already checked
    ``schedule.override`` for an ``override``."""
    locked = await lock_sessions(session, [class_session_id])
    if not locked:
        raise RecordNotFoundError()
    row = locked[0]
    ensure_version(row, body.version)
    now = clock.now()
    pending = await pending_substitution_of(session, row.id)
    refusal = substitution_refusal(
        row.state, row.starts_at, now, has_pending=pending is not None
    )
    if refusal == "state":
        raise ClassSessionNotSubstitutableError(refusal, state=row.state)
    if refusal == "substitution_pending" and pending is not None:
        raise ClassSessionNotSubstitutableError(
            refusal, substitution_id=str(pending.id)
        )
    if refusal is not None:
        raise ClassSessionNotSubstitutableError(refusal)
    original_tutor_id = row.tutor_user_id
    assert original_tutor_id is not None  # noqa: S101 - confirmed has a tutor
    # #63 D5: the substitute's tutor lock, before the zone and the checks
    # (the #52 C2 position).
    await lock_tutor(session, body.substitute_tutor_user_id)
    zone = await administration.get_center_time_zone(session, for_share=True)

    errors: list[FieldError] = []
    substitute = await identity.get_role_member(
        session, TUTOR_ROLE, body.substitute_tutor_user_id
    )
    if substitute is None:
        errors.append(_substitute_error("tutor_unknown"))
    elif substitute.state != ACTIVE:
        errors.append(_substitute_error("tutor_inactive"))
    elif substitute.id == original_tutor_id:
        errors.append(_substitute_error("substitute_is_current_tutor"))
    policy = await administration.get_scheduling_policy(session, now)
    respond_by = None
    try:
        respond_by = resolve_respond_by(
            policy,
            offered_at=now,
            first_starts_at=row.starts_at,
            requested=body.respond_by,
        )
    except RespondByInvalidError:
        errors.append(respond_by_error())
    if errors:
        raise SessionValidationError(errors)
    # Narrowing only: every invalid value was rejected above.
    assert substitute is not None and respond_by is not None  # noqa: S101

    conflicts = await find_offer_conflicts(
        session, row, substitute.id, zone, check_center_closed=row.series_id is None
    )
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([conflict.as_wire() for conflict in conflicts])
    if decision.overridden:
        assert body.override is not None  # noqa: S101 - only an allowed override
        await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=actor_id,
            class_series_id=row.series_id,
        )
    warnings = await session_warnings(
        session, substitute.id, row.starts_at, row.ends_at, zone
    )

    offer = await insert_offer(
        session,
        tutor_user_id=substitute.id,
        offered_by_user_id=actor_id,
        offered_at=now,
        respond_by=respond_by,
        class_session_ids=[row.id],
        kind=OFFER_KIND_SUBSTITUTION,
    )
    substitution = ClassSessionSubstitution(
        id=uuid.uuid7(),
        class_session_id=row.id,
        offer_id=offer.id,
        original_tutor_user_id=original_tutor_id,
        substitute_tutor_user_id=substitute.id,
        reason=body.reason,
        requested_by_user_id=actor_id,
        slot_starts_at=row.starts_at,
        slot_ends_at=row.ends_at,
    )
    session.add(substitution)
    await session.flush()

    subject = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]
    original = (await identity.get_account_references(session, [original_tutor_id]))[
        original_tutor_id
    ]
    await notify_offer_received(
        session,
        offer,
        subject_name=subject.name,
        starts_at=row.starts_at,
        original_tutor_name=original.display_name,
    )
    await _close_earlier_items(session, row.id, by_user_id=actor_id)
    [item] = await substitution_items(session, [(substitution, offer)])
    return SubstitutionCreated(substitution=item, warnings=warnings)


async def withdraw_substitution(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    substitution_id: uuid.UUID,
) -> SubstitutionItem:
    """``POST …/substitutions/{substitution_id}/withdraw`` (B5)."""
    substitution: ClassSessionSubstitution | None = await session.scalar(
        select(ClassSessionSubstitution).where(
            ClassSessionSubstitution.id == substitution_id,
            ClassSessionSubstitution.class_session_id == class_session_id,
        )
    )
    if substitution is None:
        raise RecordNotFoundError()
    await lock_sessions(session, [class_session_id])
    offer = await _lock_offer(session, substitution.offer_id)
    if offer.status != OFFER_PENDING:
        raise OfferNotPendingError(offer.status)
    offer.status = OFFER_WITHDRAWN
    offer.responded_at = clock.now()
    await session.flush()
    [item] = await substitution_items(session, [(substitution, offer)])
    return item


async def list_session_substitutions(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSessionSubstitutions:
    """``GET /api/v1/class-sessions/{id}/substitutions`` (E2): newest first;
    ``404`` for an unknown session; three statements."""
    exists = await session.scalar(
        select(ClassSession.id).where(ClassSession.id == class_session_id)
    )
    if exists is None:
        raise RecordNotFoundError()
    rows = (
        await session.execute(
            select(ClassSessionSubstitution, AssignmentOffer)
            .join(
                AssignmentOffer,
                AssignmentOffer.id == ClassSessionSubstitution.offer_id,
            )
            .where(ClassSessionSubstitution.class_session_id == class_session_id)
            .order_by(AssignmentOffer.offered_at.desc(), AssignmentOffer.id.desc())
        )
    ).all()
    return ClassSessionSubstitutions(
        items=await substitution_items(session, [(sub, offer) for sub, offer in rows])
    )
