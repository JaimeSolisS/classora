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

**Withdraw** (:func:`withdraw_pending_offers`): a seam for the later paths
that cancel or substitute an ``offered`` session (#55, #58, M4-07b): the
covering pending offers become ``withdrawn``, without an inbox item or a
notification. The caller holds the session locks and moves the sessions.

A decline or expiry whose sessions are all no longer ``offered`` (moved by
a later path) changes only the offer: there is nothing to reassign, so no
inbox item or notification is made.

**Reads** (:func:`list_my_offers`, :func:`list_session_offers`): a page of
offers in at most six statements whatever its size.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
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
from app.modules.families import service as families
from app.modules.identity import service as identity
from app.modules.scheduling.booking import (
    find_offer_conflicts,
    respond_by_error,
)
from app.modules.scheduling.conflicts import TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    ClassSessionNotOfferableError,
    OfferExpiredError,
    OfferNotPendingError,
    SchedulingConflictError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.models import (
    OFFER_ACCEPTED,
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_PENDING,
    OFFER_WITHDRAWN,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSession,
    ClassSessionParticipant,
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
from app.modules.scheduling.schemas import (
    AssignmentOfferCreate,
    AssignmentOfferCreated,
    AssignmentOfferItem,
    AssignmentOfferPage,
    ClassSessionOffers,
    OfferPerson,
    OfferSession,
    OfferSessionClassroom,
    OfferSessionParticipant,
    OfferSessionSubject,
    OfferStatusFilter,
)
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState
from app.modules.scheduling.workload_sessions import session_warnings

REASSIGNMENT_TYPE: Final = "scheduling.offer_reassignment"
"""The approval type of the reassignment inbox item (#52 E1, E2)."""

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"
CLOSED_STATUSES: Final = (OFFER_DECLINED, OFFER_EXPIRED)


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
    latest declined or expired offer, if it is still open."""
    latest = await session.scalar(
        select(AssignmentOffer.id)
        .join(
            AssignmentOfferSession,
            AssignmentOfferSession.offer_id == AssignmentOffer.id,
        )
        .where(AssignmentOfferSession.class_session_id == class_session_id)
        .where(AssignmentOffer.status.in_(CLOSED_STATUSES))
        .order_by(AssignmentOffer.responded_at.desc(), AssignmentOffer.id.desc())
        .limit(1)
    )
    if latest is None:
        return
    request_id = await administration.approval_request_id_for_subject(
        session, request_type=REASSIGNMENT_TYPE, subject_id=latest
    )
    if request_id is None:
        return
    try:
        await administration.cancel_approval_request(
            session, request_id=request_id, by_user_id=by_user_id
        )
    except administration.ApprovalTransitionInvalidError:
        # Already resolved or dismissed: nothing is open. The check runs
        # before any write, so the transaction is unaffected.
        return


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
    # #63 seam: once scheduling.locks.lock_tutor exists (M4-18), take it
    # here for body.tutor_user_id, before the zone and the checks.
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
) -> None:
    """Decline or expire: the still-offered sessions → ``unassigned``, the
    offer ``status``, then (when a session went back) the reassignment item
    and the staff notifications."""
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
    await session.flush()
    if not returned:
        return
    first = min(returned, key=lambda r: (r.starts_at, r.id))
    await administration.create_approval_request(
        session,
        request_type=REASSIGNMENT_TYPE,
        subject_id=offer.id,
        requester_user_id=None,
        priority=reassignment_priority(first.starts_at, now),
    )
    subject = (await catalog.subject_references(session, [first.subject_id]))[
        first.subject_id
    ]
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
        class_session_id=first.id,
        subject_name=subject.name,
        starts_at=first.starts_at,
        tutor_name=tutor.display_name,
    )


async def answer_offer(
    session: AsyncSession,
    offer_id: uuid.UUID,
    *,
    caller_id: uuid.UUID,
    accept: bool,
    note: str | None = None,
) -> AssignmentOfferItem:
    """``POST /api/v1/assignment-offers/{id}/accept|decline`` (D1-D3)."""
    found = await session.scalar(
        select(AssignmentOffer.tutor_user_id).where(AssignmentOffer.id == offer_id)
    )
    if found is None or found != caller_id:
        raise RecordNotFoundError()
    sessions = await lock_sessions(
        session, await covered_session_ids(session, offer_id)
    )
    offer = await _lock_offer(session, offer_id)
    if offer.status != OFFER_PENDING:
        raise OfferNotPendingError(offer.status)
    now = clock.now()
    if is_overdue(offer.respond_by, now):
        raise OfferExpiredError()
    if accept:
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
        await session.flush()
    else:
        await _close_offer(
            session, offer, sessions, status=OFFER_DECLINED, now=now, note=note
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
    sessions = await lock_sessions(
        session, await covered_session_ids(session, offer_id)
    )
    offer = await _lock_offer(session, offer_id)
    if offer.status != OFFER_PENDING or not is_overdue(offer.respond_by, now):
        return False
    await _close_offer(session, offer, sessions, status=OFFER_EXPIRED, now=now)
    return True


async def withdraw_pending_offers(
    session: AsyncSession,
    class_session_ids: Sequence[uuid.UUID],
    *,
    by_user_id: uuid.UUID | None,
) -> list[uuid.UUID]:
    """E4 seam (#55, #58, M4-07b): mark the pending offers covering these
    sessions ``withdrawn`` (no inbox item, no notification) and return their
    ids. The caller has locked the sessions (the lock order) and moves them
    out of ``offered`` itself. ``by_user_id`` is the audit actor of the
    caller's Unit of Work; it is not stored on the offer."""
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


# --- Reads (G) -----------------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


async def offer_items(
    session: AsyncSession, offers: Sequence[AssignmentOffer]
) -> list[AssignmentOfferItem]:
    """The items of ``offers``, in their order: at most five statements (the
    covered sessions with their students, then the students, subjects,
    classrooms and accounts by reference)."""
    if not offers:
        return []
    students = (
        select(func.array_agg(ClassSessionParticipant.student_id))
        .where(ClassSessionParticipant.class_session_id == ClassSession.id)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(AssignmentOfferSession.offer_id, ClassSession, students)
            .join(
                ClassSession, ClassSession.id == AssignmentOfferSession.class_session_id
            )
            .where(AssignmentOfferSession.offer_id.in_([offer.id for offer in offers]))
            .order_by(ClassSession.starts_at, ClassSession.id)
        )
    ).all()
    student_ids = {sid for row in rows for sid in (row[2] or ())}
    student_refs = await families.student_references(session, student_ids)
    subjects = await catalog.subject_references(
        session, {row[1].subject_id for row in rows}
    )
    rooms = await catalog.classroom_references(
        session, {row[1].classroom_id for row in rows if row[1].classroom_id}
    )
    accounts = await identity.get_account_references(
        session,
        {o.tutor_user_id for o in offers} | {o.offered_by_user_id for o in offers},
    )
    by_offer: dict[uuid.UUID, list[OfferSession]] = {}
    for offer_id, row, ids in rows:
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

    return [
        AssignmentOfferItem(
            id=offer.id,
            scope="session",
            status=offer.status,  # type: ignore[arg-type]
            tutor=person(offer.tutor_user_id),
            offered_at=_utc(offer.offered_at),
            offered_by=person(offer.offered_by_user_id),
            respond_by=_utc(offer.respond_by),
            responded_at=None
            if offer.responded_at is None
            else _utc(offer.responded_at),
            decline_note=offer.decline_note,
            sessions=by_offer.get(offer.id, []),
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
    ``pending`` by deadline, otherwise newest first. At most six
    statements (the total rides on the page query)."""
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
            select(AssignmentOffer, func.count().over())
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
    return AssignmentOfferPage(
        items=await offer_items(session, offers),
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
