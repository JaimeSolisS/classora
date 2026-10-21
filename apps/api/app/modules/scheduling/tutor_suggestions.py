"""Tutor suggestions for a new slot and replacements for a session (#51 D,
E, F; the rules are :mod:`~app.modules.scheduling.eligibility`).

:func:`suggest_tutors` is the one implementation behind both routes (and
for #52 / #55). In order:

1. the approved qualifications for the subject
   (``staff.service.qualified_tutor_ids``), minus the excluded tutors;
2. their accounts (``identity.service.get_account_references``): only
   ``active`` users holding the ``tutor`` role stay;
3. the overlap: **one statement** for all of them (a session as tutor in
   ``TUTOR_OCCUPYING_STATES`` with ``starts_at < end AND ends_at > start``,
   the excluded session left out);
4. availability, per candidate, with the shared #50 reader and rule
   (``staff.service.tutor_availability_rules``, two statements, then the
   pure ``staff.service.evaluate_interval`` with no closed dates: a center
   closure is a slot conflict, never a tutor one, #51 B2);
5. the workload: **one statement** with conditional sums over the slot's
   local ISO week and date;
6. the preferred classrooms, per candidate, only when the slot has a room;
7. the ranking, then the limits (``staff.service.teaching_limits``) for the
   returned items only.

At most ``8 + 6 × C`` statements (``C`` the candidates after step 2).
Read-only: no lock, no audit event, nothing written (#51 F3). The routes'
reads (:func:`suggest_for_slot`, :func:`suggest_replacements`) add the
``422`` / ``404`` / ``409`` checks.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.identity import service as identity
from app.modules.scheduling.eligibility import (
    Candidate,
    local_day_bounds,
    local_week_bounds,
    rank_candidates,
)
from app.modules.scheduling.errors import (
    ClassSessionNotAssignableError,
    InvalidSessionTimesError,
    SessionValidationError,
)
from app.modules.scheduling.models import ClassSession
from app.modules.scheduling.schemas import (
    TUTOR_SUGGESTIONS_DEFAULT_LIMIT,
    SessionTutor,
    TutorSuggestion,
    TutorSuggestionList,
    TutorSuggestionQuery,
)
from app.modules.scheduling.states import TUTOR_OCCUPYING_STATES, SessionState
from app.modules.scheduling.times import validate_session_bounds
from app.modules.staff import service as staff

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"
NOT_ASSIGNABLE_STATES: Final = frozenset(
    {SessionState.COMPLETED, SessionState.CANCELLED, SessionState.SKIPPED_CLOSURE}
)
"""A session in these states takes no tutor (#51 E3)."""
NO_CLOSED_DATES: Final[frozenset[date]] = frozenset()
"""Center closures never drop a tutor (#51 B2): the create/offer path
reports them as ``center_closed``."""
_ONE_MICROSECOND: Final = timedelta(microseconds=1)

_OCCUPYING: Final = sorted(state.value for state in TUTOR_OCCUPYING_STATES)

_MESSAGES: Final = {
    "naive_datetime": "Give the time with its UTC offset.",
    "not_whole_minutes": "Use whole minutes.",
    "ends_before_start": "The end must be after the start.",
    "duration_out_of_range": "A session lasts 15 to 480 minutes.",
    "crosses_local_date": "A session starts and ends on the same local date.",
    "subject_unknown": "Choose an existing subject.",
    "subject_inactive": "The subject or its education level is inactive.",
    "classroom_unknown": "Choose an existing classroom.",
    "classroom_inactive": "The classroom is inactive.",
}


def _query_error(field: str, code: str) -> FieldError:
    return {"path": ["query", field], "code": code, "message": _MESSAGES[code]}


def _is_naive(value: datetime) -> bool:
    return value.tzinfo is None or value.utcoffset() is None


def _bounds_field(reason: str, starts_at: datetime) -> str:
    # The #48 C2 step 5 mapping, on the query's fields.
    if reason == "naive_datetime":
        return "starts_at" if _is_naive(starts_at) else "ends_at"
    if reason == "not_whole_minutes":
        return "starts_at" if starts_at.second or starts_at.microsecond else "ends_at"
    return "ends_at"


# --- The shared implementation (F1) --------------------------------------------------


async def _busy_tutors(
    session: AsyncSession,
    tutor_ids: Collection[uuid.UUID],
    starts_at: datetime,
    ends_at: datetime,
    exclude_session_id: uuid.UUID | None,
) -> set[uuid.UUID]:
    """The tutors among ``tutor_ids`` already holding an overlapping session
    (one statement)."""
    query = (
        select(ClassSession.tutor_user_id)
        .where(
            ClassSession.tutor_user_id.in_(list(tutor_ids)),
            ClassSession.state.in_(_OCCUPYING),
            ClassSession.starts_at < ends_at,
            ClassSession.ends_at > starts_at,
        )
        .distinct()
    )
    if exclude_session_id is not None:
        query = query.where(ClassSession.id != exclude_session_id)
    return {row for row in await session.scalars(query) if row is not None}


async def _workload(
    session: AsyncSession,
    tutor_ids: Collection[uuid.UUID],
    starts_at: datetime,
    zone: str,
    exclude_session_id: uuid.UUID | None,
) -> dict[uuid.UUID, tuple[int, int]]:
    """``(weekly_minutes, daily_minutes)`` by tutor (C1; one statement,
    conditional sums; tutors without sessions are left out)."""
    local_date = starts_at.astimezone(ZoneInfo(zone)).date()
    week_start, week_end = local_week_bounds(local_date, zone)
    day_start, day_end = local_day_bounds(local_date, zone)
    in_day = (ClassSession.starts_at >= day_start) & (ClassSession.starts_at < day_end)
    query = (
        select(
            ClassSession.tutor_user_id,
            func.sum(ClassSession.scheduled_minutes),
            func.sum(case((in_day, ClassSession.scheduled_minutes), else_=0)),
        )
        .where(
            ClassSession.tutor_user_id.in_(list(tutor_ids)),
            ClassSession.state.in_(_OCCUPYING),
            ClassSession.starts_at >= week_start,
            ClassSession.starts_at < week_end,
        )
        .group_by(ClassSession.tutor_user_id)
    )
    if exclude_session_id is not None:
        query = query.where(ClassSession.id != exclude_session_id)
    rows = await session.execute(query)
    return {
        tutor_id: (int(weekly or 0), int(daily or 0))
        for tutor_id, weekly, daily in rows
        if tutor_id is not None
    }


async def suggest_tutors(
    session: AsyncSession,
    *,
    subject_id: uuid.UUID,
    starts_at: datetime,
    ends_at: datetime,
    classroom_id: uuid.UUID | None = None,
    exclude_session_id: uuid.UUID | None = None,
    exclude_tutor_user_ids: Collection[uuid.UUID] = frozenset(),
    limit: int = TUTOR_SUGGESTIONS_DEFAULT_LIMIT,
) -> TutorSuggestionList:
    """The eligible tutors for the slot in ranking order (the module
    docstring; the rules in :mod:`~app.modules.scheduling.eligibility`). The
    caller has validated the bounds and the references. Never writes."""
    qualified = await staff.qualified_tutor_ids(session, subject_id)
    candidate_ids = set(qualified) - set(exclude_tutor_user_ids)
    accounts = {
        user_id: account
        for user_id, account in (
            await identity.get_account_references(session, candidate_ids)
        ).items()
        if account.state == ACTIVE and TUTOR_ROLE in account.roles
    }
    if not accounts:
        return TutorSuggestionList(items=[], eligible_count=0)

    busy = await _busy_tutors(
        session, accounts.keys(), starts_at, ends_at, exclude_session_id
    )
    free = [user_id for user_id in accounts if user_id not in busy]
    zone = await administration.get_center_time_zone(session)
    tz = ZoneInfo(zone)
    first_date = starts_at.astimezone(tz).date()
    # The last instant inside the slot: an end at the next midnight is still
    # the start's date.
    last_date = (ends_at - _ONE_MICROSECOND).astimezone(tz).date()
    available: list[uuid.UUID] = []
    for user_id in sorted(free):
        rules = await staff.tutor_availability_rules(
            session, user_id, first_date, last_date
        )
        result = staff.evaluate_interval(
            rules.windows, rules.exceptions, NO_CLOSED_DATES, starts_at, ends_at, zone
        )
        if result.available:
            available.append(user_id)
    if not available:
        return TutorSuggestionList(items=[], eligible_count=0)

    workload = await _workload(session, available, starts_at, zone, exclude_session_id)
    candidates = []
    for user_id in available:
        prefers = classroom_id is not None and classroom_id in (
            await staff.preferred_classroom_ids(session, user_id)
        )
        weekly, daily = workload.get(user_id, (0, 0))
        candidates.append(
            Candidate(
                user_id=user_id,
                display_name=accounts[user_id].display_name,
                weekly_minutes=weekly,
                daily_minutes=daily,
                prefers_classroom=prefers,
            )
        )
    ranked = rank_candidates(candidates)
    items = []
    for rank, candidate in enumerate(ranked[:limit], start=1):
        limits = await staff.teaching_limits(session, candidate.user_id)
        items.append(
            TutorSuggestion(
                rank=rank,
                tutor=SessionTutor(
                    id=candidate.user_id, display_name=candidate.display_name
                ),
                weekly_minutes=candidate.weekly_minutes,
                daily_minutes=candidate.daily_minutes,
                weekly_limit_minutes=limits.weekly_limit_minutes,
                daily_limit_minutes=limits.daily_limit_minutes,
                prefers_classroom=candidate.prefers_classroom,
            )
        )
    return TutorSuggestionList(items=items, eligible_count=len(ranked))


# --- The routes' reads (D, E) --------------------------------------------------------


async def _validate_references(
    session: AsyncSession, query: TutorSuggestionQuery
) -> None:
    errors: list[FieldError] = []
    subject = await catalog.get_subject_for_reference(
        session, query.subject_id, lock=False
    )
    if subject is None:
        errors.append(_query_error("subject_id", "subject_unknown"))
    elif not (subject.is_active and subject.education_level_is_active):
        errors.append(_query_error("subject_id", "subject_inactive"))
    if query.classroom_id is not None:
        classroom = (
            await catalog.classroom_references(session, [query.classroom_id])
        ).get(query.classroom_id)
        if classroom is None:
            errors.append(_query_error("classroom_id", "classroom_unknown"))
        elif not classroom.is_active:
            errors.append(_query_error("classroom_id", "classroom_inactive"))
    if errors:
        raise SessionValidationError(errors)


async def suggest_for_slot(
    session: AsyncSession, query: TutorSuggestionQuery
) -> TutorSuggestionList:
    """``GET /api/v1/tutor-suggestions`` (D2): the zone (a plain read:
    advisory), ``422`` bounds, ``422`` references (all at once), then
    :func:`suggest_tutors`."""
    zone = await administration.get_center_time_zone(session)
    try:
        validate_session_bounds(query.starts_at, query.ends_at, zone)
    except InvalidSessionTimesError as exc:
        field = _bounds_field(exc.reason, query.starts_at)
        raise SessionValidationError([_query_error(field, exc.reason)]) from None
    await _validate_references(session, query)
    return await suggest_tutors(
        session,
        subject_id=query.subject_id,
        starts_at=query.starts_at,
        ends_at=query.ends_at,
        classroom_id=query.classroom_id,
        limit=query.limit,
    )


async def suggest_replacements(
    session: AsyncSession, class_session_id: uuid.UUID, *, limit: int
) -> TutorSuggestionList:
    """``GET /api/v1/class-sessions/{id}/replacement-tutors`` (E): ``404``
    for an unknown id, ``409 class_session_not_assignable`` for a final or
    skipped one, then :func:`suggest_tutors` for its own slot without itself
    and without its current tutor."""
    row = await session.get(ClassSession, class_session_id)
    if row is None:
        raise RecordNotFoundError()
    if SessionState(row.state) in NOT_ASSIGNABLE_STATES:
        raise ClassSessionNotAssignableError()
    excluded = frozenset() if row.tutor_user_id is None else {row.tutor_user_id}
    return await suggest_tutors(
        session,
        subject_id=row.subject_id,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        classroom_id=row.classroom_id,
        exclude_session_id=row.id,
        exclude_tutor_user_ids=excluded,
        limit=limit,
    )
