"""Offering a recurring series to a tutor in one offer (#233 C; spec W1.3;
ADR-0005 D3, D9).

A **series offer** (``scope = 'series'``) covers every not-yet-started
occurrence of a series from a local ``from_date`` onward. The series row
holds the assignment (#233 E1): ``tutor_user_id``, ``assignment_status``
(``pending`` while the offer waits, ``accepted`` after) and
``assignment_from``. :func:`~app.modules.scheduling.materialize.
occurrence_initial_state` reads them, so occurrences created later are
``offered`` (and attached to the pending offer) or ``confirmed``.

**At creation** (:func:`offer_new_series`, ``POST /api/v1/class-series``
with a tutor): the series is inserted with ``pending`` from ``starts_on``,
materialized, and then its offer is made over every ``offered`` row (the
``skipped_closure`` rows are not covered).

**Later** (:func:`offer_class_series`, ``POST
/api/v1/class-series/{id}/offers``), only for a series **without a tutor**
(after a create without one, or a series decline or expiry), after the
route's ``401``/``403`` (``schedule.assign``) and the body's ``422`` (an
``override`` is ``422``: M4-09c):

1. the series (no lock): ``404 not_found``;
2. the locks (#233 B3): ``lock_students`` (the series' participants), the
   zone ``FOR SHARE``, for a cohort's ``course`` series the cohort row
   ``FOR UPDATE`` (``courses.service.lock_cohort``, #65 C5, G1), the series
   row ``FOR UPDATE`` (re-read);
3. ``409 version_conflict`` on the series ``version``;
4. ``409 series_not_offerable``: ``details.reason = "assigned"`` when the
   series has a tutor (pending or accepted), ``"ended"`` when ``from_date``
   is after ``last_occurrence_on`` or no rule occurrence on or after it is
   still to come (:func:`~app.modules.scheduling.recurrence.
   first_covered_start`);
5. ``422 from_date_in_past`` when ``from_date`` is before the local today;
   the default is the later of ``starts_on`` and the local today;
6. the **covered rows**: the series' ``unassigned`` rows whose rule instant
   is on a local date on or after ``from_date`` and that start after now,
   locked by id. Rows in any other state are not covered;
7. ``422`` for the tutor (``tutor_unknown``, ``tutor_inactive``) and
   ``respond_by`` (``respond_by_invalid``), in one response;
8. the #52 offer checks on every covered row for the new tutor
   (``center_closed`` off, as for every series occurrence): any conflict is
   ``409 scheduling_conflict`` per occurrence (``details.occurrence_start``,
   ``details.date``); nothing is written;
9. every covered row ``unassigned → offered`` (an ``EXCLUDE`` race → ``409
   scheduling_conflict`` with one ``tutor_overlap``), the series assignment
   (``pending`` from ``from_date``; its version is bumped), the offer and
   its rows, the tutor's ``scheduling.offer_received``, and the open
   reassignment items of the series' earlier declined or expired series
   offers are cancelled by the caller;
10. ``201`` with the offer and ``warnings: []``.

**Deadline** (#233 B5): ``resolve_respond_by`` with the earliest covered
row's start, or the rule's next occurrence when no row is covered yet (a
series beyond the horizon); a later row never moves it.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Sequence
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError, SchedulingConflict
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.identity import service as identity
from app.modules.scheduling.booking import respond_by_error
from app.modules.scheduling.checks import find_offer_conflicts
from app.modules.scheduling.conflicts import TUTOR_OVERLAP, Conflict
from app.modules.scheduling.errors import (
    SchedulingConflictError,
    SeriesNotOfferableError,
    SessionOverlapError,
    SessionValidationError,
)
from app.modules.scheduling.locks import lock_students, lock_tutor
from app.modules.scheduling.materialize import (
    occurrence_local_date,
    series_participant_ids,
)
from app.modules.scheduling.models import (
    OFFER_DECLINED,
    OFFER_EXPIRED,
    OFFER_SCOPE_SERIES,
    SERIES_ASSIGNMENT_PENDING,
    AssignmentOffer,
    ClassSeries,
    ClassSession,
)
from app.modules.scheduling.offer_flow import (
    ACTIVE,
    TUTOR_ROLE,
    close_reassignment_item_of,
    lock_series,
    lock_sessions,
    offer_items,
)
from app.modules.scheduling.offer_records import (
    insert_offer,
    notify_offer_received,
    rfc3339,
)
from app.modules.scheduling.offers import RespondByInvalidError, resolve_respond_by
from app.modules.scheduling.recurrence import first_covered_start
from app.modules.scheduling.schemas import (
    AssignmentOfferCreated,
    ClassSeriesOfferCreate,
)
from app.modules.scheduling.sessions import transition_class_session
from app.modules.scheduling.states import SessionState


def default_from_date(starts_on: date, today: date) -> date:
    """C2 step 7: the offer covers from the later of the series' start and
    the local today."""
    return max(starts_on, today)


def next_covered_start(
    series: ClassSeries, from_date: date, now: datetime
) -> datetime | None:
    """B4 for ``series``: its next rule occurrence on or after
    ``from_date``."""
    return first_covered_start(
        series.rrule,
        starts_on=series.starts_on,
        start_time=series.start_time,
        zone=series.time_zone,
        from_date=from_date,
        now=now,
    )


async def series_respond_by(
    session: AsyncSession,
    *,
    offered_at: datetime,
    first_starts_at: datetime,
    requested: datetime | None,
) -> datetime:
    """B5: the series offer's deadline (:class:`RespondByInvalidError` for a
    requested one that is not after now or later than the default)."""
    policy = await administration.get_scheduling_policy(session, offered_at)
    return resolve_respond_by(
        policy,
        offered_at=offered_at,
        first_starts_at=first_starts_at,
        requested=requested,
    )


async def _make_offer(
    session: AsyncSession,
    series: ClassSeries,
    rows: Sequence[ClassSession],
    *,
    first_starts_at: datetime,
    respond_by: datetime,
    offered_at: datetime,
    actor_id: uuid.UUID,
) -> AssignmentOffer:
    """The pending series offer over ``rows`` and the tutor's notification
    (the series assignment is already set)."""
    assert series.tutor_user_id is not None  # noqa: S101 - set by the caller
    assert series.assignment_from is not None  # noqa: S101
    offer = await insert_offer(
        session,
        tutor_user_id=series.tutor_user_id,
        offered_by_user_id=actor_id,
        offered_at=offered_at,
        respond_by=respond_by,
        class_session_ids=[row.id for row in rows],
        class_series_id=series.id,
        from_date=series.assignment_from,
    )
    subject = (await catalog.subject_references(session, [series.subject_id]))[
        series.subject_id
    ]
    await notify_offer_received(
        session,
        offer,
        subject_name=subject.name,
        starts_at=first_starts_at,
        occurrence_count=len(rows),
    )
    return offer


# --- C1: a series created with a tutor ---------------------------------------------


async def offer_new_series(
    session: AsyncSession,
    series: ClassSeries,
    *,
    offered_at: datetime,
    requested_respond_by: datetime | None,
    actor_id: uuid.UUID,
) -> AssignmentOffer:
    """C1, after the create's materialization: the pending series offer over
    every ``offered`` row of the new series (``from_date = starts_on``). The
    requested deadline was validated before the expansion
    (:func:`validate_create_respond_by`), so it is valid here too."""
    rows = list(
        await session.scalars(
            select(ClassSession)
            .where(
                ClassSession.series_id == series.id,
                ClassSession.state == SessionState.OFFERED,
            )
            .order_by(ClassSession.starts_at, ClassSession.id)
        )
    )
    first = (
        rows[0].starts_at
        if rows
        else next_covered_start(series, series.starts_on, offered_at)
    )
    if first is None:  # pragma: no cover - validate_create_respond_by refused it
        raise SeriesNotOfferableError("ended")
    try:
        respond_by = await series_respond_by(
            session,
            offered_at=offered_at,
            first_starts_at=first,
            requested=requested_respond_by,
        )
    except RespondByInvalidError:
        raise SessionValidationError([respond_by_error()]) from None
    return await _make_offer(
        session,
        series,
        rows,
        first_starts_at=first,
        respond_by=respond_by,
        offered_at=offered_at,
        actor_id=actor_id,
    )


async def validate_create_respond_by(
    session: AsyncSession,
    series: ClassSeries,
    *,
    offered_at: datetime,
    requested: datetime | None,
) -> None:
    """C1, before the expansion: a series with a tutor must have an
    occurrence still to come (``409 series_not_offerable``, ``ended``), and a
    requested ``respond_by`` must be valid against the rule's next
    occurrence (``422 respond_by_invalid``; the first covered row is never
    earlier)."""
    first = next_covered_start(series, series.starts_on, offered_at)
    if first is None:
        raise SeriesNotOfferableError("ended")
    if requested is None:
        return
    try:
        await series_respond_by(
            session, offered_at=offered_at, first_starts_at=first, requested=requested
        )
    except RespondByInvalidError:
        raise SessionValidationError([respond_by_error()]) from None


# --- C2: offering a series without a tutor -----------------------------------------


def _from_date_error() -> FieldError:
    return {
        "path": ["body", "from_date"],
        "code": "from_date_in_past",
        "message": "The offer can't start before today.",
    }


def _tutor_error(code: str) -> FieldError:
    messages = {
        "tutor_unknown": "Choose an existing tutor.",
        "tutor_inactive": "The tutor's account is not active.",
    }
    return {"path": ["body", "tutor_user_id"], "code": code, "message": messages[code]}


def _occurrence_wire(
    conflict: Conflict, row: ClassSession, zone: str
) -> SchedulingConflict:
    assert row.original_start is not None  # noqa: S101 - a series row
    wire = conflict.as_wire()
    wire["details"] = {
        **wire["details"],
        "occurrence_start": rfc3339(row.original_start),
        "date": occurrence_local_date(row, zone).isoformat(),
    }
    return wire


async def _covered_rows(
    session: AsyncSession, series: ClassSeries, from_date: date, now: datetime
) -> list[ClassSession]:
    """Step 8: the ``unassigned`` rows from ``from_date`` that start after
    ``now``, locked by id (re-checked under the lock)."""
    candidates = await session.scalars(
        select(ClassSession).where(
            ClassSession.series_id == series.id,
            ClassSession.state == SessionState.UNASSIGNED,
            ClassSession.starts_at > now,
        )
    )
    ids = [
        row.id
        for row in candidates
        if occurrence_local_date(row, series.time_zone) >= from_date
    ]
    locked = await lock_sessions(session, ids)
    return sorted(
        (
            row
            for row in locked
            if row.state == SessionState.UNASSIGNED and row.starts_at > now
        ),
        key=lambda row: (row.starts_at, row.id),
    )


async def _close_earlier_items(
    session: AsyncSession, series_id: uuid.UUID, *, by_user_id: uuid.UUID
) -> None:
    """Step 15: cancel the open reassignment items of the series' earlier
    declined or expired series offers."""
    offer_ids = await session.scalars(
        select(AssignmentOffer.id)
        .where(
            AssignmentOffer.class_series_id == series_id,
            AssignmentOffer.scope == OFFER_SCOPE_SERIES,
            AssignmentOffer.status.in_((OFFER_DECLINED, OFFER_EXPIRED)),
        )
        .order_by(AssignmentOffer.id)
    )
    for offer_id in list(offer_ids):
        await close_reassignment_item_of(session, offer_id, by_user_id=by_user_id)


async def offer_class_series(
    session: AsyncSession,
    class_series_id: uuid.UUID,
    body: ClassSeriesOfferCreate,
    *,
    actor_id: uuid.UUID,
) -> AssignmentOfferCreated:
    """``POST /api/v1/class-series/{id}/offers``: see the module docstring
    for the order."""
    found = await session.get(ClassSeries, class_series_id)
    if found is None:
        raise RecordNotFoundError()
    await lock_students(session, await series_participant_ids(session, found.id))
    zone = await administration.get_center_time_zone(session, for_share=True)
    if found.cohort_id is not None:
        # #65 C5, G1: a cohort's series is offered under the cohort lock.
        await courses.lock_cohort(session, found.cohort_id)
    series = await lock_series(session, found.id)
    ensure_version(series, body.version)
    if series.tutor_user_id is not None:
        raise SeriesNotOfferableError("assigned")
    now = clock.now()
    today = clock.today_in(zone)
    from_date = body.from_date or default_from_date(series.starts_on, today)
    next_start = next_covered_start(series, from_date, now)
    if next_start is None or (
        series.last_occurrence_on is not None and from_date > series.last_occurrence_on
    ):
        raise SeriesNotOfferableError("ended")
    if from_date < today:
        raise SessionValidationError([_from_date_error()])
    rows = await _covered_rows(session, series, from_date, now)

    # #63 D5: the tutor lock, before the checks.
    await lock_tutor(session, body.tutor_user_id)
    errors: list[FieldError] = []
    tutor = await identity.get_role_member(session, TUTOR_ROLE, body.tutor_user_id)
    if tutor is None:
        errors.append(_tutor_error("tutor_unknown"))
    elif tutor.state != ACTIVE:
        errors.append(_tutor_error("tutor_inactive"))
    first = rows[0].starts_at if rows else next_start
    respond_by = None
    try:
        respond_by = await series_respond_by(
            session, offered_at=now, first_starts_at=first, requested=body.respond_by
        )
    except RespondByInvalidError:
        errors.append(respond_by_error())
    if errors:
        raise SessionValidationError(errors)
    # Narrowing only: every invalid value was rejected above.
    assert tutor is not None and respond_by is not None  # noqa: S101

    refused: list[SchedulingConflict] = []
    for row in rows:
        conflicts = await find_offer_conflicts(
            session, row, tutor.id, zone, check_center_closed=False
        )
        refused += [_occurrence_wire(c, row, zone) for c in conflicts]
    if refused:
        raise SchedulingConflictError(refused)

    for row in rows:
        try:
            await transition_class_session(
                session,
                row.id,
                to_state=SessionState.OFFERED,
                expected_version=row.version,
                tutor_user_id=tutor.id,
            )
        except SessionOverlapError:
            raise SchedulingConflictError(
                [Conflict(TUTOR_OVERLAP, {"class_session_ids": []}).as_wire()]
            ) from None
    series.tutor_user_id = tutor.id
    series.assignment_status = SERIES_ASSIGNMENT_PENDING
    series.assignment_from = from_date
    await session.flush()
    offer = await _make_offer(
        session,
        series,
        rows,
        first_starts_at=first,
        respond_by=respond_by,
        offered_at=now,
        actor_id=actor_id,
    )
    await _close_earlier_items(session, series.id, by_user_id=actor_id)
    [item] = await offer_items(session, [offer])
    return AssignmentOfferCreated(offer=item, warnings=[])
