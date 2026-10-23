"""Offering, answering, expiring and reading assignment offers (#52 C2, D,
E1, E3, E4, G; spec W1.3-W1.4; ADR-0005 D2, D3, D9).

Every writer follows the lock order and keeps the invariant of
:mod:`~app.modules.scheduling.offers` (sessions ``FOR UPDATE`` in ascending
id, then the offer ``FOR UPDATE``; ``offered`` iff exactly one pending
offer).

**Offer an ``unassigned`` session** (:func:`offer_class_session`,
``POST /api/v1/class-sessions/{id}/offers``), after the route's
``401``/``403`` (``schedule.assign``; ``schedule.override`` for an
``override``) and the body's ``422``:

1. the session ``FOR UPDATE`` → ``404 not_found``;
2. ``409 version_conflict`` on ``version``;
3. ``409 class_session_not_offerable`` (``details.state``) unless
   ``unassigned``, then (``details.reason = "started"``) when it has begun;
4. the zone ``FOR SHARE``; ``422`` for the tutor (``tutor_unknown``,
   ``tutor_inactive``) and ``respond_by`` (``respond_by_invalid``), in one
   response;
5. the checks of :func:`~app.modules.scheduling.booking.find_offer_conflicts`
   (the new tutor and the slot checks that can change after creation; not
   ``notice_window``, the student or room overlaps or capacity);
6. the override decision (#49): ``409 scheduling_conflict`` with every
   conflict unless none, or all soft and listed;
7. the tutor's workload warnings for the slot (#50; the session doesn't
   hold the tutor yet, so it never counts itself);
8. ``unassigned → offered`` (an ``EXCLUDE`` race → ``409
   scheduling_conflict`` with one ``tutor_overlap``), the override record
   when something was overridden, the offer and its session row, the
   tutor's ``scheduling.offer_received``, and the session's open
   reassignment item is cancelled (by the offering user);
9. ``201`` with the offer and the warnings.

**Answer** (:func:`answer_offer`; the offered tutor only): an unknown offer,
or another caller's, is ``404`` (never ``403``) → the lock order → ``409
offer_not_pending`` (``details.status``) → ``409 offer_expired`` from
``respond_by`` on → accept: every covered ``offered`` session →
``confirmed``; decline: → ``unassigned`` (the tutor cleared, the room and
students still held), the note, the reassignment item (E1) and the
``scheduling.offer_declined`` notifications.

**Expire** (:func:`expire_offer`, one offer per Unit of Work; the periodic
task in :mod:`~app.modules.scheduling.tasks`): the same as a decline with
``expired`` and ``scheduling.offer_expired``, after re-checking under the
locks that the offer is still pending and overdue (else nothing changes).

**Series offers** (#233 D; the route is in
:mod:`~app.modules.scheduling.series_offers`): the answer and expiry paths
take the series row ``FOR UPDATE`` first (the #233 B3 order). Accepting
confirms the covered rows still ``offered`` and marks the series
``accepted``; declining or expiring returns them to ``unassigned``, clears
the series' tutor and assignment, and always raises one inbox item and the
staff notifications (with the series params), even when no row was
covered.

**Substitutions** (#55 D; created by
:mod:`~app.modules.scheduling.substitution_flow`): the same answer and
expiry paths, with the session lock then the offer lock. Accepting
re-checks the session against the substitution's snapshot (``409
substitution_no_longer_valid``) and swaps the tutor
(:func:`~app.modules.scheduling.sessions.replace_session_tutor`; an
``EXCLUDE`` refusal is ``409 scheduling_conflict`` with one
``tutor_overlap``); declining or expiring leaves the session ``confirmed``
with its original tutor and raises the inbox item and the staff
notifications. :func:`withdraw_pending_substitutions` is the G3 seam of
the writers that move a ``confirmed`` row.

**Withdraw** (:func:`withdraw_pending_offers`): a seam for the later paths
that cancel or substitute an ``offered`` session (#55, #58): the covering
pending **session-scope** offers become ``withdrawn``, without an inbox item
or a notification. The caller holds the session locks and moves the
sessions. :func:`withdraw_series_offer` (#54, #55) withdraws a series'
pending series offer from the series row on.

A session-scope decline or expiry whose sessions are all no longer
``offered`` (moved by a later path) changes only the offer: there is
nothing to reassign, so no inbox item or notification is made.

**Reads** (:func:`list_my_offers`, :func:`list_session_offers`): a page of
offers in at most seven statements whatever its size (#233 F1: a series
offer lists its first ten sessions, with ``session_count``).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import respond_by_error
from app.modules.scheduling.checks import find_offer_conflicts
from app.modules.scheduling.conflicts import TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    ClassSessionNotOfferableError,
    OfferExpiredError,
    OfferNotPendingError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
    SubstitutionNoLongerValidError,
)
from app.modules.scheduling.locks import lock_tutor
from app.modules.scheduling.models import (
    OFFER_ACCEPTED,
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_KIND_ASSIGNMENT,
    OFFER_KIND_SUBSTITUTION,
    OFFER_PENDING,
    OFFER_SCOPE_SESSION,
    OFFER_WITHDRAWN,
    SERIES_ASSIGNMENT_ACCEPTED,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSeries,
    ClassSession,
    ClassSessionParticipant,
    ClassSessionSubstitution,
)
from app.modules.scheduling.offer_records import (
    OFFER_DECLINED as OFFER_DECLINED_NOTIFICATION,
)
from app.modules.scheduling.offer_records import (
    OFFER_EXPIRED as OFFER_EXPIRED_NOTIFICATION,
)
from app.modules.scheduling.offer_records import (
    insert_offer,
    notify_offer_closed,
    notify_offer_received,
    pending_series_offer,
)
from app.modules.scheduling.offers import (
    ANSWERED_STATUSES,
    RespondByInvalidError,
    can_transition_offer,
    is_overdue,
    reassignment_priority,
    resolve_respond_by,
)
from app.modules.scheduling.overrides import evaluate_override, record_session_override
from app.modules.scheduling.recurrence import first_covered_start
from app.modules.scheduling.schemas import (
    AssignmentOfferCreate,
    AssignmentOfferCreated,
    AssignmentOfferItem,
    AssignmentOfferPage,
    ClassSessionOffers,
    OfferCohort,
    OfferPerson,
    OfferSeries,
    OfferSession,
    OfferSessionClassroom,
    OfferSessionParticipant,
    OfferSessionSubject,
    OfferStatusFilter,
    OfferSubstitution,
)
from app.modules.scheduling.sessions import (
    replace_session_tutor,
    transition_class_session,
)
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.substitution_records import (
    SESSION_TUTOR_CHANGED,
    TUTOR_SUBSTITUTED,
    family_recipients,
    notify_session,
    substitution_of_offer,
)
from app.modules.scheduling.substitutions import substitution_still_valid
from app.modules.scheduling.workload_sessions import session_warnings

REASSIGNMENT_TYPE: Final = "scheduling.offer_reassignment"
"""The approval type of the reassignment inbox item (#52 E1, E2)."""

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"
CLOSED_STATUSES: Final = (OFFER_DECLINED, OFFER_EXPIRED)
OFFER_ITEM_SESSIONS_MAX: Final = 10
"""A series offer item lists at most this many covered sessions (#233 F1)."""


# --- Locks (A4) ----------------------------------------------------------------------


async def covered_session_ids(
    session: AsyncSession, offer_id: uuid.UUID
) -> list[uuid.UUID]:
    """The ids of the sessions ``offer_id`` covers, ascending."""
    rows = await session.scalars(
        select(AssignmentOfferSession.class_session_id)
        .where(AssignmentOfferSession.offer_id == offer_id)
        .order_by(AssignmentOfferSession.class_session_id)
    )
    return list(rows)


async def lock_sessions(
    session: AsyncSession, class_session_ids: Sequence[uuid.UUID]
) -> list[ClassSession]:
    """The sessions ``FOR UPDATE``, in ascending id (the first step of the
    lock order)."""
    if not class_session_ids:
        return []
    rows = await session.scalars(
        select(ClassSession)
        .where(ClassSession.id.in_(sorted(set(class_session_ids))))
        .order_by(ClassSession.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def lock_series(session: AsyncSession, class_series_id: uuid.UUID) -> ClassSeries:
    """The series row ``FOR UPDATE`` (#233 B3 step 3), re-read."""
    series = await session.scalar(
        select(ClassSeries)
        .where(ClassSeries.id == class_series_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if series is None:
        raise RecordNotFoundError()
    return series


async def _lock_for_answer(
    session: AsyncSession, offer_id: uuid.UUID, class_series_id: uuid.UUID | None
) -> tuple[ClassSeries | None, list[ClassSession], AssignmentOffer]:
    """The lock order of an answer, expiry or withdrawal: for a series offer
    the series row first (#233 B3), then the covered sessions by id, then
    the offer."""
    series = (
        None if class_series_id is None else await lock_series(session, class_series_id)
    )
    sessions = await lock_sessions(
        session, await covered_session_ids(session, offer_id)
    )
    return series, sessions, await _lock_offer(session, offer_id)


def clear_series_assignment(series: ClassSeries) -> None:
    """The series has no tutor any more (#233 D3, B6): its later
    occurrences are created ``unassigned``."""
    series.tutor_user_id = None
    series.assignment_status = None
    series.assignment_from = None


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


def _finish(offer: AssignmentOffer, status: str, now: datetime) -> None:
    if not can_transition_offer(offer.status, status):  # pragma: no cover
        raise ValueError(f"offer {offer.status} -> {status}")
    offer.status = status
    offer.responded_at = now


# --- Offer an unassigned session (C2) ------------------------------------------------


def _tutor_error(code: str) -> FieldError:
    messages = {
        "tutor_unknown": "Choose an existing tutor.",
        "tutor_inactive": "The tutor's account is not active.",
    }
    return {"path": ["body", "tutor_user_id"], "code": code, "message": messages[code]}


async def _validate_offer_body(
    session: AsyncSession, row: ClassSession, body: AssignmentOfferCreate, now: datetime
) -> tuple[identity.RoleMember, datetime]:
    errors: list[FieldError] = []
    tutor = await identity.get_role_member(session, TUTOR_ROLE, body.tutor_user_id)
    if tutor is None:
        errors.append(_tutor_error("tutor_unknown"))
    elif tutor.state != ACTIVE:
        errors.append(_tutor_error("tutor_inactive"))
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
    assert tutor is not None and respond_by is not None  # noqa: S101
    return tutor, respond_by


async def _close_reassignment_item(
    session: AsyncSession, class_session_id: uuid.UUID, *, by_user_id: uuid.UUID
) -> None:
    """E2 auto-close: cancel the open reassignment item of the session's
    latest declined or expired **session-scope assignment** offer, if it is
    still open. The items of series offers are closed by the series route
    (#233 C3), those of substitution offers by the next substitution (#55
    B2 step 12) or the cancel."""
    latest = await session.scalar(
        select(AssignmentOffer.id)
        .join(
            AssignmentOfferSession,
            AssignmentOfferSession.offer_id == AssignmentOffer.id,
        )
        .where(AssignmentOfferSession.class_session_id == class_session_id)
        .where(AssignmentOffer.scope == OFFER_SCOPE_SESSION)
        .where(AssignmentOffer.kind == OFFER_KIND_ASSIGNMENT)
        .where(AssignmentOffer.status.in_(CLOSED_STATUSES))
        .order_by(AssignmentOffer.responded_at.desc(), AssignmentOffer.id.desc())
        .limit(1)
    )
    if latest is not None:
        await close_reassignment_item_of(session, latest, by_user_id=by_user_id)


async def close_reassignment_item_of(
    session: AsyncSession, offer_id: uuid.UUID, *, by_user_id: uuid.UUID | None
) -> uuid.UUID | None:
    """Cancel the reassignment item of ``offer_id`` if it is still open;
    the id of the cancelled item, or ``None``."""
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=REASSIGNMENT_TYPE, subject_id=offer_id
    )
    if request_id is None:
        return None
    try:
        await administration.cancel_approval_request(
            session, request_id=request_id, by_user_id=by_user_id
        )
    except administration.ApprovalTransitionInvalidError:
        # Already resolved or dismissed: nothing is open. The check runs
        # before any write, so the transaction is unaffected.
        return None
    return request_id


async def offer_class_session(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    body: AssignmentOfferCreate,
    *,
    actor_id: uuid.UUID,
) -> AssignmentOfferCreated:
    """``POST /api/v1/class-sessions/{id}/offers``: see the module
    docstring for the order. The route has already checked
    ``schedule.override`` for an ``override``."""
    locked = await lock_sessions(session, [class_session_id])
    if not locked:
        raise RecordNotFoundError()
    row = locked[0]
    ensure_version(row, body.version)
    if row.state != SessionState.UNASSIGNED:
        raise ClassSessionNotOfferableError(state=row.state)
    now = clock.now()
    if row.starts_at <= now:
        raise ClassSessionNotOfferableError(started=True)
    # #63 D5: the tutor lock, before the zone and the checks.
    await lock_tutor(session, body.tutor_user_id)
    zone = await administration.get_center_time_zone(session, for_share=True)
    tutor, respond_by = await _validate_offer_body(session, row, body, now)

    conflicts = await find_offer_conflicts(session, row, tutor.id, zone)
    decision = evaluate_override(conflicts, body.override)
    if not decision.allowed:
        raise SchedulingConflictError([conflict.as_wire() for conflict in conflicts])
    warnings = await session_warnings(
        session, tutor.id, row.starts_at, row.ends_at, zone
    )

    try:
        await transition_class_session(
            session,
            row.id,
            to_state=SessionState.OFFERED,
            expected_version=body.version,
            tutor_user_id=tutor.id,
        )
    except SessionOverlapError:
        raise SchedulingConflictError(
            [Conflict(TUTOR_OVERLAP, {"class_session_ids": []}).as_wire()]
        ) from None
    if decision.overridden:
        # Only an allowed override reaches here, so body.override is set.
        assert body.override is not None  # noqa: S101
        await record_session_override(
            session,
            row.id,
            decision.overridden,
            body.override.reason,
            actor_id=actor_id,
        )
    offer = await insert_offer(
        session,
        tutor_user_id=tutor.id,
        offered_by_user_id=actor_id,
        offered_at=now,
        respond_by=respond_by,
        class_session_ids=[row.id],
    )
    subject = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]
    await notify_offer_received(
        session, offer, subject_name=subject.name, starts_at=row.starts_at
    )
    await _close_reassignment_item(session, row.id, by_user_id=actor_id)
    [item] = await offer_items(session, [offer])
    return AssignmentOfferCreated(offer=item, warnings=warnings)


# --- Answer (D), close (E1) and withdraw (E4) ----------------------------------------


async def _close_offer(
    session: AsyncSession,
    offer: AssignmentOffer,
    sessions: Sequence[ClassSession],
    *,
    status: str,
    now: datetime,
    note: str | None = None,
    series: ClassSeries | None = None,
) -> None:
    """Decline or expire: the still-offered sessions → ``unassigned``, the
    offer ``status``, then (when a session went back) the reassignment item
    and the staff notifications. A series offer (#233 D3) also clears the
    series' tutor and always raises the item and the notifications: the
    series itself needs a tutor again.

    A **substitution** offer (#55 D2) leaves its session as it is (still
    ``confirmed`` with the original tutor) and raises the item and the
    notifications while the session is still ``confirmed``."""
    if offer.kind == OFFER_KIND_SUBSTITUTION:
        await _close_substitution(
            session, offer, sessions, status=status, now=now, note=note
        )
        return
    returned: list[ClassSession] = []
    for row in sessions:
        if (
            row.state == SessionState.OFFERED
            and row.tutor_user_id == offer.tutor_user_id
        ):
            await transition_class_session(
                session,
                row.id,
                to_state=SessionState.UNASSIGNED,
                expected_version=row.version,
            )
            returned.append(row)
    _finish(offer, status, now)
    offer.decline_note = note
    if series is not None:
        clear_series_assignment(series)
    await session.flush()
    first = min(returned, key=lambda r: (r.starts_at, r.id)) if returned else None
    if first is not None:
        subject_id, first_start = first.subject_id, first.starts_at
    elif series is not None:
        subject_id = series.subject_id
        first_start = _series_first_start(series, offer, now) or offer.respond_by
    else:
        return
    await administration.create_approval_request(
        session,
        request_type=REASSIGNMENT_TYPE,
        subject_id=offer.id,
        requester_user_id=None,
        priority=reassignment_priority(first_start, now),
    )
    subject = (await catalog.subject_references(session, [subject_id]))[subject_id]
    tutor = (await identity.get_account_references(session, [offer.tutor_user_id]))[
        offer.tutor_user_id
    ]
    await notify_offer_closed(
        session,
        offer,
        notification_type=(
            OFFER_DECLINED_NOTIFICATION
            if status == OFFER_DECLINED
            else OFFER_EXPIRED_NOTIFICATION
        ),
        class_session_id=None if first is None else first.id,
        subject_name=subject.name,
        starts_at=first_start,
        tutor_name=tutor.display_name,
        occurrence_count=len(returned),
    )


async def _close_substitution(
    session: AsyncSession,
    offer: AssignmentOffer,
    sessions: Sequence[ClassSession],
    *,
    status: str,
    now: datetime,
    note: str | None,
) -> None:
    _finish(offer, status, now)
    offer.decline_note = note
    await session.flush()
    [row] = sessions
    if row.state != SessionState.CONFIRMED:
        return
    substitution = await substitution_of_offer(session, offer.id)
    assert substitution is not None  # noqa: S101 - A3: one row per offer
    await administration.create_approval_request(
        session,
        request_type=REASSIGNMENT_TYPE,
        subject_id=offer.id,
        requester_user_id=None,
        priority=reassignment_priority(row.starts_at, now),
    )
    subject = (await catalog.subject_references(session, [row.subject_id]))[
        row.subject_id
    ]
    accounts = await identity.get_account_references(
        session, {offer.tutor_user_id, substitution.original_tutor_user_id}
    )
    await notify_offer_closed(
        session,
        offer,
        notification_type=(
            OFFER_DECLINED_NOTIFICATION
            if status == OFFER_DECLINED
            else OFFER_EXPIRED_NOTIFICATION
        ),
        class_session_id=row.id,
        subject_name=subject.name,
        starts_at=row.starts_at,
        tutor_name=accounts[offer.tutor_user_id].display_name,
        original_tutor_name=accounts[substitution.original_tutor_user_id].display_name,
    )


async def _accept_substitution(
    session: AsyncSession,
    offer: AssignmentOffer,
    sessions: Sequence[ClassSession],
    *,
    now: datetime,
) -> None:
    """#55 D1: re-check the session against the snapshot (``409
    substitution_no_longer_valid``), swap the tutor (an ``EXCLUDE`` refusal
    → ``409 scheduling_conflict`` with one ``tutor_overlap``), accept the
    offer and tell the original tutor and the family. Qualification and
    availability are not re-checked (#52 D2)."""
    [row] = sessions
    substitution = await substitution_of_offer(session, offer.id)
    assert substitution is not None  # noqa: S101 - A3: one row per offer
    if not substitution_still_valid(
        state=row.state,
        tutor_user_id=row.tutor_user_id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        original_tutor_user_id=substitution.original_tutor_user_id,
        slot_starts_at=substitution.slot_starts_at,
        slot_ends_at=substitution.slot_ends_at,
    ):
        raise SubstitutionNoLongerValidError()
    try:
        await replace_session_tutor(
            session,
            row.id,
            tutor_user_id=offer.tutor_user_id,
            expected_version=row.version,
        )
    except SessionOverlapError:
        raise SchedulingConflictError(
            [Conflict(TUTOR_OVERLAP, {"class_session_ids": []}).as_wire()]
        ) from None
    _finish(offer, OFFER_ACCEPTED, now)
    await session.flush()
    await notify_session(
        session,
        [substitution.original_tutor_user_id],
        TUTOR_SUBSTITUTED,
        class_session_id=row.id,
        starts_at=row.starts_at,
        actor_id=offer.tutor_user_id,
    )
    await notify_session(
        session,
        await family_recipients(session, row.id),
        SESSION_TUTOR_CHANGED,
        class_session_id=row.id,
        starts_at=row.starts_at,
        actor_id=offer.tutor_user_id,
    )


def _series_first_start(
    series: ClassSeries, offer: AssignmentOffer, now: datetime
) -> datetime | None:
    """B4 for the offer's ``from_date``: the next rule occurrence it covers."""
    assert offer.from_date is not None  # noqa: S101 - a series offer has one
    return first_covered_start(
        series.rrule,
        starts_on=series.starts_on,
        start_time=series.start_time,
        zone=series.time_zone,
        from_date=offer.from_date,
        now=now,
    )


async def _offer_target(
    session: AsyncSession, offer_id: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID | None, str] | None:
    """``(tutor_user_id, class_series_id, kind)`` of an offer (unlocked;
    they never change), or ``None`` when there is no such offer."""
    found = (
        await session.execute(
            select(
                AssignmentOffer.tutor_user_id,
                AssignmentOffer.class_series_id,
                AssignmentOffer.kind,
            ).where(AssignmentOffer.id == offer_id)
        )
    ).first()
    return None if found is None else (found[0], found[1], found[2])


async def answer_offer(
    session: AsyncSession,
    offer_id: uuid.UUID,
    *,
    caller_id: uuid.UUID,
    accept: bool,
    note: str | None = None,
) -> AssignmentOfferItem:
    """``POST /api/v1/assignment-offers/{id}/accept|decline`` (D1-D3; a
    series offer, #233 D; a substitution offer, #55 D)."""
    found = await _offer_target(session, offer_id)
    if found is None or found[0] != caller_id:
        raise RecordNotFoundError()
    series, sessions, offer = await _lock_for_answer(session, offer_id, found[1])
    if offer.status != OFFER_PENDING:
        raise OfferNotPendingError(offer.status)
    now = clock.now()
    if is_overdue(offer.respond_by, now):
        raise OfferExpiredError()
    if accept and offer.kind == OFFER_KIND_SUBSTITUTION:
        await _accept_substitution(session, offer, sessions, now=now)
    elif accept:
        for row in sessions:
            if (
                row.state == SessionState.OFFERED
                and row.tutor_user_id == offer.tutor_user_id
            ):
                await transition_class_session(
                    session,
                    row.id,
                    to_state=SessionState.CONFIRMED,
                    expected_version=row.version,
                )
        _finish(offer, OFFER_ACCEPTED, now)
        if series is not None:
            # D2: later occurrences are created confirmed (B1).
            series.assignment_status = SERIES_ASSIGNMENT_ACCEPTED
        await session.flush()
    else:
        await _close_offer(
            session,
            offer,
            sessions,
            status=OFFER_DECLINED,
            now=now,
            note=note,
            series=series,
        )
    [item] = await offer_items(session, [offer])
    return item


async def overdue_offer_ids(
    session: AsyncSession, now: datetime, *, limit: int
) -> list[uuid.UUID]:
    """The pending offers whose deadline has passed at ``now``, oldest
    deadline first, at most ``limit`` (the partial index serves it)."""
    rows = await session.scalars(
        select(AssignmentOffer.id)
        .where(AssignmentOffer.status == OFFER_PENDING)
        .where(AssignmentOffer.respond_by <= now)
        .order_by(AssignmentOffer.respond_by, AssignmentOffer.id)
        .limit(limit)
    )
    return list(rows)


async def expire_offer(
    session: AsyncSession, offer_id: uuid.UUID, now: datetime
) -> bool:
    """E3, one offer: under the lock order, re-check that it is still pending
    and overdue at ``now`` (else ``False``, nothing changes: a concurrent
    answer won, or a re-run), then close it as ``expired``."""
    found = await _offer_target(session, offer_id)
    if found is None:  # pragma: no cover - offers are never deleted
        return False
    series, sessions, offer = await _lock_for_answer(session, offer_id, found[1])
    if offer.status != OFFER_PENDING or not is_overdue(offer.respond_by, now):
        return False
    await _close_offer(
        session, offer, sessions, status=OFFER_EXPIRED, now=now, series=series
    )
    return True


async def withdraw_pending_offers(
    session: AsyncSession,
    class_session_ids: Sequence[uuid.UUID],
    *,
    by_user_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """E4 seam (#55, #58): mark the pending **session-scope** offers of
    **either kind** (an assignment of an ``offered`` session, a
    substitution of a ``confirmed`` one; #55 G2) covering these sessions
    ``withdrawn`` (no inbox item, no notification) and return their ids.
    The caller has locked the sessions (the lock order) and moves them out
    of ``offered`` itself. Series-scope offers are left alone
    (:func:`withdraw_series_offer` withdraws one; #233 B6). ``by_user_id``
    is the audit actor of the caller's Unit of Work; it is not stored on the
    offer."""
    del by_user_id
    if not class_session_ids:
        return []
    offer_ids = list(
        await session.scalars(
            select(AssignmentOffer.id)
            .join(
                AssignmentOfferSession,
                AssignmentOfferSession.offer_id == AssignmentOffer.id,
            )
            .where(AssignmentOfferSession.class_session_id.in_(class_session_ids))
            .where(AssignmentOffer.status == OFFER_PENDING)
            .where(AssignmentOffer.scope == OFFER_SCOPE_SESSION)
            .distinct()
            .order_by(AssignmentOffer.id)
        )
    )
    now = clock.now()
    for offer_id in offer_ids:
        offer = await _lock_offer(session, offer_id)
        if offer.status == OFFER_PENDING:
            _finish(offer, OFFER_WITHDRAWN, now)
    await session.flush()
    return offer_ids


async def withdraw_pending_substitutions(
    session: AsyncSession, class_session_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """#55 G3: the writers that move a ``confirmed`` row (``PATCH``, the
    series edits) withdraw its pending **substitution** (its snapshot no
    longer matches) and keep any assignment offer (#54 caps its deadline).
    The caller holds the session locks; the offers are locked here. Returns
    the withdrawn offer ids."""
    if not class_session_ids:
        return []
    offer_ids = list(
        await session.scalars(
            select(AssignmentOffer.id)
            .join(
                AssignmentOfferSession,
                AssignmentOfferSession.offer_id == AssignmentOffer.id,
            )
            .where(AssignmentOfferSession.class_session_id.in_(class_session_ids))
            .where(AssignmentOffer.status == OFFER_PENDING)
            .where(AssignmentOffer.kind == OFFER_KIND_SUBSTITUTION)
            .order_by(AssignmentOffer.id)
        )
    )
    now = clock.now()
    for offer_id in offer_ids:
        offer = await _lock_offer(session, offer_id)
        if offer.status == OFFER_PENDING:
            _finish(offer, OFFER_WITHDRAWN, now)
    await session.flush()
    return offer_ids


async def cap_offer_deadlines(
    session: AsyncSession, class_session_ids: Sequence[uuid.UUID]
) -> list[uuid.UUID]:
    """#54 B6 seam (D3): after the caller moved these sessions, every pending
    offer (either scope) covering one of them gets ``respond_by = min(
    respond_by, first covered start)``, the first start among its covered
    sessions that are still ``offered``. The caller has locked the sessions
    (the lock order); the offers are locked here, by id. Returns the ids of
    the offers whose deadline moved."""
    if not class_session_ids:
        return []
    offer_ids = list(
        await session.scalars(
            select(AssignmentOffer.id)
            .join(
                AssignmentOfferSession,
                AssignmentOfferSession.offer_id == AssignmentOffer.id,
            )
            .where(AssignmentOfferSession.class_session_id.in_(class_session_ids))
            .where(AssignmentOffer.status == OFFER_PENDING)
            .distinct()
            .order_by(AssignmentOffer.id)
        )
    )
    moved: list[uuid.UUID] = []
    for offer_id in offer_ids:
        offer = await _lock_offer(session, offer_id)
        first = await session.scalar(
            select(func.min(ClassSession.starts_at))
            .join(
                AssignmentOfferSession,
                AssignmentOfferSession.class_session_id == ClassSession.id,
            )
            .where(
                AssignmentOfferSession.offer_id == offer_id,
                ClassSession.state == SessionState.OFFERED,
            )
        )
        if offer.status == OFFER_PENDING and first is not None:
            if first < offer.respond_by:
                offer.respond_by = first
                moved.append(offer.id)
    await session.flush()
    return moved


async def withdraw_series_offer(
    session: AsyncSession, class_series_id: uuid.UUID, *, by_user_id: uuid.UUID | None
) -> uuid.UUID | None:
    """#233 B6 seam (#54 cancellations, #55): withdraw the series' pending
    series offer under the lock order from the series row: the offer becomes
    ``withdrawn``, its still-``offered`` sessions go back to ``unassigned``
    (the tutor cleared) and the series loses its tutor and assignment. No
    inbox item and no notification. Returns the offer id, or ``None`` (and
    changes nothing) when no offer is pending. ``by_user_id`` is the audit
    actor of the caller's Unit of Work; it is not stored."""
    del by_user_id
    series = await lock_series(session, class_series_id)
    pending = await pending_series_offer(session, class_series_id)
    if pending is None:
        return None
    sessions = await lock_sessions(
        session, await covered_session_ids(session, pending.id)
    )
    offer = await _lock_offer(session, pending.id)
    if offer.status != OFFER_PENDING:  # pragma: no cover - the series lock
        return None
    for row in sessions:
        if (
            row.state == SessionState.OFFERED
            and row.tutor_user_id == offer.tutor_user_id
        ):
            await transition_class_session(
                session,
                row.id,
                to_state=SessionState.UNASSIGNED,
                expected_version=row.version,
            )
    _finish(offer, OFFER_WITHDRAWN, clock.now())
    clear_series_assignment(series)
    await session.flush()
    return offer.id


# --- Reads (G) -----------------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


async def offer_items(
    session: AsyncSession,
    offers: Sequence[AssignmentOffer],
    *,
    loaded_series: Mapping[uuid.UUID, ClassSeries] | None = None,
) -> list[AssignmentOfferItem]:
    """The items of ``offers``, in their order: at most six statements (the
    first covered sessions of each offer with their students and the
    per-offer count, the series of the series offers, then the students,
    subjects, classrooms and accounts by reference), plus the names of the
    cohorts of course series (#65 C6, ``courses.service.cohort_names``) when
    there are any. ``loaded_series`` (the series of the series offers,
    read by the caller with the page) saves the series statement. A series
    offer lists its first :data:`OFFER_ITEM_SESSIONS_MAX` sessions by start
    (#233 F1)."""
    if not offers:
        return []
    students = (
        select(func.array_agg(ClassSessionParticipant.student_id))
        .where(ClassSessionParticipant.class_session_id == ClassSession.id)
        .scalar_subquery()
    )
    ranked = (
        select(
            AssignmentOfferSession.offer_id,
            AssignmentOfferSession.class_session_id,
            func.row_number()
            .over(
                partition_by=AssignmentOfferSession.offer_id,
                order_by=(ClassSession.starts_at, ClassSession.id),
            )
            .label("position"),
            func.count()
            .over(partition_by=AssignmentOfferSession.offer_id)
            .label("total"),
        )
        .join(ClassSession, ClassSession.id == AssignmentOfferSession.class_session_id)
        .where(AssignmentOfferSession.offer_id.in_([offer.id for offer in offers]))
        .subquery()
    )
    rows = (
        await session.execute(
            select(
                ranked.c.offer_id,
                ClassSession,
                students,
                ranked.c.total,
                ClassSessionSubstitution.original_tutor_user_id,
            )
            .join(ClassSession, ClassSession.id == ranked.c.class_session_id)
            .outerjoin(
                ClassSessionSubstitution,
                ClassSessionSubstitution.offer_id == ranked.c.offer_id,
            )
            .where(ranked.c.position <= OFFER_ITEM_SESSIONS_MAX)
            .order_by(ClassSession.starts_at, ClassSession.id)
        )
    ).all()
    originals = {row[0]: row[4] for row in rows if row[4] is not None}
    series_ids = {o.class_series_id for o in offers if o.class_series_id is not None}
    if loaded_series is not None:
        series_by_id = dict(loaded_series)
    else:
        series_by_id = (
            {
                series.id: series
                for series in await session.scalars(
                    select(ClassSeries).where(ClassSeries.id.in_(series_ids))
                )
            }
            if series_ids
            else {}
        )
    cohorts = await courses.cohort_names(
        session,
        {s.cohort_id for s in series_by_id.values() if s.cohort_id is not None},
    )
    student_ids = {sid for row in rows for sid in (row[2] or ())}
    student_refs = await families.student_references(session, student_ids)
    subjects = await catalog.subject_references(
        session,
        {row[1].subject_id for row in rows}
        | {series.subject_id for series in series_by_id.values()},
    )
    rooms = await catalog.classroom_references(
        session, {row[1].classroom_id for row in rows if row[1].classroom_id}
    )
    accounts = await identity.get_account_references(
        session,
        {o.tutor_user_id for o in offers}
        | {o.offered_by_user_id for o in offers}
        | set(originals.values()),
    )
    by_offer: dict[uuid.UUID, list[OfferSession]] = {}
    totals: dict[uuid.UUID, int] = {}
    for offer_id, row, ids, total, _ in rows:
        totals[offer_id] = int(total)
        subject = subjects[row.subject_id]
        room = rooms.get(row.classroom_id) if row.classroom_id else None
        names = sorted(
            (
                student_refs[sid].display_name
                for sid in (ids or ())
                if sid in student_refs
            ),
            key=str.casefold,
        )
        by_offer.setdefault(offer_id, []).append(
            OfferSession(
                id=row.id,
                state=SessionState(row.state).value,
                starts_at=_utc(row.starts_at),
                ends_at=_utc(row.ends_at),
                subject=OfferSessionSubject(
                    name=subject.name,
                    education_level_name=subject.education_level_name,
                ),
                classroom=None
                if room is None
                else OfferSessionClassroom(name=room.name),
                participants=[OfferSessionParticipant(display_name=n) for n in names],
            )
        )

    def person(user_id: uuid.UUID) -> OfferPerson:
        account = accounts[user_id]
        return OfferPerson(id=account.id, display_name=account.display_name)

    def series_of(offer: AssignmentOffer) -> OfferSeries | None:
        series = series_by_id.get(offer.class_series_id)  # type: ignore[arg-type]
        if series is None or offer.from_date is None:
            return None
        subject = subjects[series.subject_id]
        return OfferSeries(
            id=series.id,
            kind=series.kind,  # type: ignore[arg-type]
            cohort=(
                None
                if series.cohort_id is None
                else OfferCohort(
                    id=series.cohort_id, name=cohorts.get(series.cohort_id, "")
                )
            ),
            subject=OfferSessionSubject(
                name=subject.name, education_level_name=subject.education_level_name
            ),
            weekdays=list(series.weekdays),
            interval=series.interval,
            start_time=series.start_time.strftime("%H:%M"),
            duration_minutes=series.duration_minutes,
            time_zone=series.time_zone,
            from_date=offer.from_date,
            until=series.until_on,
            count=series.count,
            last_occurrence_on=series.last_occurrence_on,
        )

    return [
        AssignmentOfferItem(
            id=offer.id,
            scope=offer.scope,  # type: ignore[arg-type]
            kind=offer.kind,  # type: ignore[arg-type]
            substitution=None
            if offer.id not in originals
            else OfferSubstitution(original_tutor=person(originals[offer.id])),
            status=offer.status,  # type: ignore[arg-type]
            tutor=person(offer.tutor_user_id),
            offered_at=_utc(offer.offered_at),
            offered_by=person(offer.offered_by_user_id),
            respond_by=_utc(offer.respond_by),
            responded_at=None
            if offer.responded_at is None
            else _utc(offer.responded_at),
            decline_note=offer.decline_note,
            series=series_of(offer),
            session_count=totals.get(offer.id, 0),
            sessions=by_offer.get(offer.id, []),
            sessions_truncated=totals.get(offer.id, 0)
            > len(by_offer.get(offer.id, [])),
        )
        for offer in offers
    ]


def _status_conditions(status: OfferStatusFilter) -> list[ColumnElement[bool]]:
    if status == "pending":
        return [AssignmentOffer.status == OFFER_PENDING]
    if status == "answered":
        return [AssignmentOffer.status.in_(sorted(ANSWERED_STATUSES))]
    return []


async def list_my_offers(
    session: AsyncSession,
    *,
    caller_id: uuid.UUID,
    status: OfferStatusFilter,
    limit: int,
    offset: int,
) -> AssignmentOfferPage:
    """``GET /api/v1/assignment-offers`` (G2): only the caller's offers;
    ``pending`` by deadline, otherwise newest first. At most seven
    statements (the total and the series of the series offers ride on the
    page query, so a cohort's name, #65 C6, fits in the budget)."""
    conditions = [
        AssignmentOffer.tutor_user_id == caller_id,
        *_status_conditions(status),
    ]
    order = (
        (AssignmentOffer.respond_by, AssignmentOffer.id)
        if status == "pending"
        else (AssignmentOffer.offered_at.desc(), AssignmentOffer.id.desc())
    )
    rows = (
        await session.execute(
            select(AssignmentOffer, func.count().over(), ClassSeries)
            .outerjoin(ClassSeries, ClassSeries.id == AssignmentOffer.class_series_id)
            .where(*conditions)
            .order_by(*order)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if rows:
        total = int(rows[0][1])
    elif offset == 0:
        total = 0
    else:
        # Past the end: no row carries the window count.
        total = int(
            await session.scalar(
                select(func.count()).select_from(AssignmentOffer).where(*conditions)
            )
            or 0
        )
    offers = [row[0] for row in rows]
    loaded = {row[2].id: row[2] for row in rows if row[2] is not None}
    return AssignmentOfferPage(
        items=await offer_items(session, offers, loaded_series=loaded),
        total=total,
        limit=limit,
        offset=offset,
    )


async def list_session_offers(
    session: AsyncSession, class_session_id: uuid.UUID
) -> ClassSessionOffers:
    """``GET /api/v1/class-sessions/{id}/offers`` (G3): the session's offers,
    newest first, decline notes included; ``404`` for an unknown session."""
    exists = await session.scalar(
        select(ClassSession.id).where(ClassSession.id == class_session_id)
    )
    if exists is None:
        raise RecordNotFoundError()
    offers = list(
        await session.scalars(
            select(AssignmentOffer)
            .join(
                AssignmentOfferSession,
                AssignmentOfferSession.offer_id == AssignmentOffer.id,
            )
            .where(AssignmentOfferSession.class_session_id == class_session_id)
            .order_by(AssignmentOffer.offered_at.desc(), AssignmentOffer.id.desc())
        )
    )
    return ClassSessionOffers(items=await offer_items(session, offers))
