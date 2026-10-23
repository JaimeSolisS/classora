"""Available slots and session requests by guardians and students (#59 B, C,
D; spec §1 "request, not booking", §3, W2.1-W2.2; ADR-0005).

Every route is ``authenticated()`` plus the family record policy
(:mod:`~app.modules.scheduling.requests_policy`, S1/S2): an unknown
student, another family's student and a student the caller doesn't see all
get the same ``404``; a linked guardian of an adult without the academic
consent gets ``403 student_consent_required``. The facts are loaded once per
request (``families.service.load_access_facts``). Nothing a family receives
names a tutor, a room, a session or another student (S3).

**Available slots** (:func:`available_slots`), after the route's ``401``,
``429`` and the schema's ``422``:

1. the business settings (zone and operating hours; two statements) and
   ``422`` for the range (``range_invalid`` / ``range_too_long`` on ``to``,
   ``range_in_past`` on ``from``, ``range_too_far`` on ``to``);
2. S1/S2;
3. ``422`` for the references (``student_inactive``, ``subject_unknown`` /
   ``subject_inactive``; no lock);
4. the search: everything is loaded once (:func:`load_family_context`) and
   the candidates are checked in memory
   (:mod:`~app.modules.scheduling.family_slots`).

Read-only, no lock, no audit; at most 14 statements whatever the range and
the number of qualified tutors.

**Create** (:func:`create_session_request`), after ``401`` / ``429`` /
schema ``422``: S1/S2 → the student lock (``lock_students``) → the zone
``FOR SHARE`` → ``422`` (``naive_datetime``, ``not_whole_minutes``,
``crosses_local_date``, ``not_on_grid`` on ``starts_at``;
``student_inactive``; ``subject_unknown`` / ``subject_inactive``) → ``409
session_request_limit_reached`` (:data:`MAX_PENDING_REQUESTS_PER_STUDENT`) →
``409 session_request_overlap`` → the slot check for exactly this time
(``409 slot_unavailable``, opaque) → the row and its
``scheduling.session_request`` approval request → ``201``. A request holds
no time: no session or participant is written, and nothing else reads a
request as occupancy. No notification.

**Withdraw** (:func:`withdraw_session_request`): ``404`` (S2) → ``403
permission_denied`` unless the caller made it → the row ``FOR UPDATE`` →
``409 version_conflict`` → ``409 session_request_not_pending`` → withdrawn,
and its inbox item cancelled (``administration.service.cancel_approval_request``).
Lock order: request, then approval request (#60's reject follows it).

**Lists filter, details hide:** the list holds the requests of every
student the caller may act for (an empty page otherwise); a request the
caller can't see is ``404``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Collection, Iterable, Mapping
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import PermissionDeniedError, Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.modules.administration import service as administration
from app.modules.administration.schemas import BusinessSettingsDetail
from app.modules.catalog import service as catalog
from app.modules.catalog.schemas import ClosureDetail, SubjectReference
from app.modules.families import service as families
from app.modules.families.service import StudentAccessFacts, StudentAgeReference
from app.modules.identity import service as identity
from app.modules.scheduling.errors import (
    InvalidSessionTimesError,
    SessionRequestLimitReachedError,
    SessionRequestNotPendingError,
    SessionRequestOverlapError,
    SessionValidationError,
    SlotUnavailableError,
    StudentConsentRequiredError,
)
from app.modules.scheduling.family_slots import (
    GRID_MINUTES,
    FamilySlotContext,
    TutorSlotFacts,
    available_days,
    family_slot_ok,
)
from app.modules.scheduling.locks import lock_students
from app.modules.scheduling.models import (
    SESSION_REQUEST_PENDING,
    SESSION_REQUEST_PENDING_OVERLAP_CONSTRAINT,
    SESSION_REQUEST_WITHDRAWN,
    ClassSession,
    ClassSessionParticipant,
    SessionRequest,
)
from app.modules.scheduling.request_schemas import (
    AvailableDay,
    AvailableSlot,
    AvailableSlots,
    AvailableSlotsQuery,
    SessionRequestCreate,
    SessionRequestDetail,
    SessionRequestItem,
    SessionRequestPage,
    SessionRequestStatusFilter,
    SessionRequestStudent,
    SessionRequestSubject,
)
from app.modules.scheduling.requests_policy import (
    FamilyAccess,
    candidate_student_ids,
    family_access,
    has_family_role,
    is_linked_guardian,
    is_own_student,
    needs_age,
)
from app.modules.scheduling.states import (
    STUDENT_OCCUPYING_STATES,
    TUTOR_OCCUPYING_STATES,
    SessionKind,
)
from app.modules.scheduling.suggestions import (
    Busy,
    Candidate,
    OperatingDay,
    SlotContext,
)
from app.modules.scheduling.times import validate_session_bounds
from app.modules.staff import service as staff

SESSION_REQUEST_TYPE: Final = "scheduling.session_request"
"""The approval type of a session request (#59 E)."""

MAX_PENDING_REQUESTS_PER_STUDENT: Final = 5
"""At most this many ``pending`` requests per student (#59 S5)."""

SLOTS_MAX_DAYS: Final = 14
SLOTS_MAX_AHEAD_DAYS: Final = 60

SESSION_REQUESTS_TIME_ZONE_DEPENDENCY: Final = "scheduling.session_requests"
"""The name of the pending-requests check in the time-zone registry (#59
A4): their instants were computed from local wall-clock picks."""

TUTOR_ROLE: Final = "tutor"
ACTIVE: Final = "active"

_TUTOR_STATES: Final = sorted(state.value for state in TUTOR_OCCUPYING_STATES)
_STUDENT_STATES: Final = sorted(state.value for state in STUDENT_OCCUPYING_STATES)

_MESSAGES: Final = {
    "range_invalid": "The last date must not be before the first.",
    "range_too_long": "Search at most 14 dates at a time.",
    "range_in_past": "The first date must not be before today.",
    "range_too_far": "Search at most 60 days ahead.",
    "naive_datetime": "Give the time with its UTC offset.",
    "not_whole_minutes": "Use whole minutes.",
    "crosses_local_date": "A session starts and ends on the same local date.",
    "not_on_grid": "Start on the hour or the half hour.",
    "student_inactive": "The student is inactive.",
    "subject_unknown": "Choose an existing subject.",
    "subject_inactive": "The subject or its education level is inactive.",
}


class _NoAssignments:
    """Family routes never use tutor assignments."""

    async def student_ids_for_tutor(
        self, session: AsyncSession, user_id: uuid.UUID
    ) -> frozenset[uuid.UUID]:
        return frozenset()


_NO_ASSIGNMENTS: Final = _NoAssignments()
_NO_FACTS: Final = StudentAccessFacts()


def _error(location: str, field: str, code: str) -> FieldError:
    return {"path": [location, field], "code": code, "message": _MESSAGES[code]}


def _local_midnight(day: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, time(0), tzinfo=tz).astimezone(UTC)


# --- S1/S2: the family record policy --------------------------------------------------


async def _facts(session: AsyncSession, principal: Principal) -> StudentAccessFacts:
    """The caller's links (no statement without a family role)."""
    if not has_family_role(principal):
        return _NO_FACTS
    return await families.load_access_facts(session, principal, _NO_ASSIGNMENTS)


async def _authorized_student(
    session: AsyncSession, principal: Principal, student_id: uuid.UUID, zone: str
) -> StudentAgeReference:
    """The student the caller may act for, or ``404`` / ``403
    student_consent_required`` (S2). A hidden student costs no extra read."""
    facts = await _facts(session, principal)
    if not (
        is_own_student(principal, student_id, facts)
        or is_linked_guardian(principal, student_id, facts)
    ):
        raise RecordNotFoundError()
    student = (await families.student_age_references(session, [student_id])).get(
        student_id
    )
    if student is None:  # pragma: no cover - a link or a login implies the row
        raise RecordNotFoundError()
    minor = (
        families.is_minor(student.birthdate, clock.today_in(zone))
        if needs_age(principal, student_id, facts)
        else None
    )
    access = family_access(principal, student_id, facts, is_minor=minor)
    if access is FamilyAccess.CONSENT_REQUIRED:
        raise StudentConsentRequiredError()
    if access is not FamilyAccess.ALLOWED:  # pragma: no cover - checked above
        raise RecordNotFoundError()
    return student


async def _allowed_students(
    session: AsyncSession,
    principal: Principal,
    facts: StudentAccessFacts,
    wanted: Collection[uuid.UUID],
) -> dict[uuid.UUID, StudentAgeReference]:
    """Of ``wanted`` (already limited to the caller's own and linked
    students), those the caller may act for, with their references."""
    if not wanted:
        return {}
    refs = await families.student_age_references(session, wanted)
    today: date | None = None
    allowed: dict[uuid.UUID, StudentAgeReference] = {}
    for student_id, ref in refs.items():
        minor = None
        if needs_age(principal, student_id, facts):
            if today is None:
                today = clock.today_in(
                    await administration.get_center_time_zone(session)
                )
            minor = families.is_minor(ref.birthdate, today)
        if family_access(principal, student_id, facts, is_minor=minor) is (
            FamilyAccess.ALLOWED
        ):
            allowed[student_id] = ref
    return allowed


# --- The slot context (B5) -----------------------------------------------------------


async def _busy(
    session: AsyncSession,
    student_id: uuid.UUID,
    tutor_ids: Collection[uuid.UUID],
    low: datetime,
    high: datetime,
) -> tuple[tuple[Busy, ...], dict[uuid.UUID, list[Busy]]]:
    """The student's occupying sessions and the tutors' occupying sessions
    overlapping ``[low, high)``, in **one** statement."""
    participates = (
        exists()
        .where(
            ClassSessionParticipant.class_session_id == ClassSession.id,
            ClassSessionParticipant.student_id == student_id,
        )
        .correlate(ClassSession)
    )
    mine = and_(ClassSession.state.in_(_STUDENT_STATES), participates)
    condition = mine
    if tutor_ids:
        condition = or_(
            mine,
            and_(
                ClassSession.tutor_user_id.in_(list(tutor_ids)),
                ClassSession.state.in_(_TUTOR_STATES),
            ),
        )
    rows = await session.execute(
        select(
            ClassSession.id,
            ClassSession.tutor_user_id,
            ClassSession.state,
            ClassSession.starts_at,
            ClassSession.ends_at,
            participates.label("mine"),
        )
        .where(ClassSession.starts_at < high, ClassSession.ends_at > low, condition)
        .order_by(ClassSession.starts_at, ClassSession.id)
    )
    student: list[Busy] = []
    tutors: dict[uuid.UUID, list[Busy]] = {tutor: [] for tutor in tutor_ids}
    for session_id, tutor_id, state, starts_at, ends_at, is_mine in rows:
        busy = Busy(session_id, starts_at, ends_at)
        if is_mine and state in _STUDENT_STATES:
            student.append(busy)
        if tutor_id in tutors and state in _TUTOR_STATES:
            tutors[tutor_id].append(busy)
    return tuple(student), tutors


async def _pending_intervals(
    session: AsyncSession, student_id: uuid.UUID, low: datetime, high: datetime
) -> tuple[tuple[datetime, datetime], ...]:
    rows = await session.execute(
        select(SessionRequest.starts_at, SessionRequest.ends_at).where(
            SessionRequest.student_id == student_id,
            SessionRequest.status == SESSION_REQUEST_PENDING,
            SessionRequest.starts_at < high,
            SessionRequest.ends_at > low,
        )
    )
    return tuple((starts_at, ends_at) for starts_at, ends_at in rows)


def _closed_dates(
    closures: Iterable[ClosureDetail], first: date, last: date
) -> frozenset[date]:
    closed: set[date] = set()
    for closure in closures:
        day = max(closure.starts_on, first)
        while day <= min(closure.ends_on, last):
            closed.add(day)
            day += timedelta(days=1)
    return frozenset(closed)


async def load_family_context(
    session: AsyncSession,
    *,
    zone: str,
    operating_hours: Iterable[OperatingDay],
    student_id: uuid.UUID,
    subject_id: uuid.UUID,
    first: date,
    last: date,
) -> FamilySlotContext:
    """Everything the in-memory check needs for ``[first, last]`` (local
    dates), loaded once (B5): the qualified tutors (one statement), their
    accounts (one), their rules (two, batch), the student's and the tutors'
    occupying sessions (one), the closures (one), the policy in force now
    (one) and the student's pending requests (one)."""
    tz = ZoneInfo(zone)
    low = _local_midnight(first, tz)
    high = _local_midnight(last + timedelta(days=1), tz)
    qualified = await staff.qualified_tutor_ids(session, subject_id)
    accounts = await identity.get_account_references(session, qualified)
    tutor_ids = sorted(
        user_id
        for user_id, account in accounts.items()
        if account.state == ACTIVE and TUTOR_ROLE in account.roles
    )
    rules = await staff.tutor_availability_rules_many(session, tutor_ids, first, last)
    student_sessions, tutor_sessions = await _busy(
        session, student_id, tutor_ids, low, high
    )
    closures = await catalog.closures_between(session, first, last)
    now = clock.now()
    policy = await administration.get_scheduling_policy(session, now)
    pending = await _pending_intervals(session, student_id, low, high)
    slot = SlotContext(
        zone=zone,
        kind=SessionKind.INDIVIDUAL.value,
        student_id=student_id,
        subject_id=subject_id,
        tutor_user_id=None,
        classroom_id=None,
        student_sessions=student_sessions,
        tutor_sessions=(),
        room_sessions=(),
        tutor_rules=None,
        tutor_qualified=True,
        limits=staff.TeachingLimits(),
        room_windows=(),
        room_capacity=None,
        closed_dates=_closed_dates(closures, first, last),
        operating_hours=tuple(operating_hours),
        policy=policy,
        now=now,
    )
    return FamilySlotContext(
        slot=slot,
        tutors=tuple(
            TutorSlotFacts(
                user_id=tutor,
                rules=rules[tutor],
                sessions=tuple(tutor_sessions[tutor]),
            )
            for tutor in tutor_ids
        ),
        pending=pending,
    )


def _operating_days(settings: BusinessSettingsDetail) -> list[OperatingDay]:
    return [
        OperatingDay(item.weekday, item.opens_at, item.closes_at)
        for item in settings.operating_hours
    ]


# --- B: available slots --------------------------------------------------------------


def _range_errors(query: AvailableSlotsQuery, today: date) -> list[FieldError]:
    if query.to < query.from_:
        return [_error("query", "to", "range_invalid")]
    if (query.to - query.from_).days + 1 > SLOTS_MAX_DAYS:
        return [_error("query", "to", "range_too_long")]
    errors: list[FieldError] = []
    if query.from_ < today:
        errors.append(_error("query", "from", "range_in_past"))
    if query.to > today + timedelta(days=SLOTS_MAX_AHEAD_DAYS):
        errors.append(_error("query", "to", "range_too_far"))
    return errors


async def _subject(
    session: AsyncSession, subject_id: uuid.UUID, *, lock: bool, location: str
) -> tuple[SubjectReference | None, list[FieldError]]:
    subject = await catalog.get_subject_for_reference(session, subject_id, lock=lock)
    if subject is None:
        return None, [_error(location, "subject_id", "subject_unknown")]
    if not (subject.is_active and subject.education_level_is_active):
        return subject, [_error(location, "subject_id", "subject_inactive")]
    return subject, []


async def available_slots(
    session: AsyncSession, principal: Principal, query: AvailableSlotsQuery
) -> AvailableSlots:
    """``GET /api/v1/available-slots``: see the module docstring."""
    settings = await administration.get_business_settings(session)
    zone = settings.time_zone
    errors = _range_errors(query, clock.today_in(zone))
    if errors:
        raise SessionValidationError(errors)
    student = await _authorized_student(session, principal, query.student_id, zone)
    errors = (
        [] if student.is_active else [_error("query", "student_id", "student_inactive")]
    )
    _, subject_errors = await _subject(
        session, query.subject_id, lock=False, location="query"
    )
    if errors + subject_errors:
        raise SessionValidationError(errors + subject_errors)
    context = await load_family_context(
        session,
        zone=zone,
        operating_hours=_operating_days(settings),
        student_id=query.student_id,
        subject_id=query.subject_id,
        first=query.from_,
        last=query.to,
    )
    days = available_days(context, query.from_, query.to, query.duration_minutes)
    return AvailableSlots(
        time_zone=zone,
        student_id=query.student_id,
        subject_id=query.subject_id,
        duration_minutes=query.duration_minutes,
        from_=query.from_,
        to=query.to,
        days=[
            AvailableDay(
                date=day,
                slots=[
                    AvailableSlot(
                        starts_at=candidate.starts_at,
                        ends_at=candidate.ends_at,
                        already_requested=requested,
                    )
                    for candidate, requested in slots
                ],
            )
            for day, slots in days
        ],
    )


# --- C: create -----------------------------------------------------------------------


def _start_errors(
    starts_at: datetime, duration_minutes: int, zone: str
) -> list[FieldError]:
    """The ``422`` codes of the start (step 7)."""
    try:
        validate_session_bounds(
            starts_at, starts_at + timedelta(minutes=duration_minutes), zone
        )
    except InvalidSessionTimesError as exc:
        return [_error("body", "starts_at", exc.reason)]
    local = starts_at.astimezone(ZoneInfo(zone))
    if local.minute % GRID_MINUTES:
        return [_error("body", "starts_at", "not_on_grid")]
    return []


async def _pending_count(session: AsyncSession, student_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count())
        .select_from(SessionRequest)
        .where(
            SessionRequest.student_id == student_id,
            SessionRequest.status == SESSION_REQUEST_PENDING,
        )
    )
    return int(count or 0)


async def _pending_overlaps(
    session: AsyncSession, student_id: uuid.UUID, starts_at: datetime, ends_at: datetime
) -> bool:
    found = await session.scalar(
        select(
            exists().where(
                SessionRequest.student_id == student_id,
                SessionRequest.status == SESSION_REQUEST_PENDING,
                SessionRequest.starts_at < ends_at,
                SessionRequest.ends_at > starts_at,
            )
        )
    )
    return bool(found)


async def create_session_request(
    session: AsyncSession, principal: Principal, body: SessionRequestCreate
) -> SessionRequestDetail:
    """``POST /api/v1/session-requests``: see the module docstring."""
    zone = await administration.get_center_time_zone(session)
    await _authorized_student(session, principal, body.student_id, zone)
    await lock_students(session, [body.student_id])
    zone = await administration.get_center_time_zone(session, for_share=True)

    errors = _start_errors(body.starts_at, body.duration_minutes, zone)
    reference = (await families.student_references(session, [body.student_id])).get(
        body.student_id
    )
    if reference is not None and not reference.is_active:
        errors.append(_error("body", "student_id", "student_inactive"))
    subject, subject_errors = await _subject(
        session, body.subject_id, lock=True, location="body"
    )
    errors += subject_errors
    if errors:
        raise SessionValidationError(errors)
    assert reference is not None and subject is not None  # noqa: S101

    starts_at = body.starts_at.astimezone(UTC)
    ends_at = starts_at + timedelta(minutes=body.duration_minutes)
    if (
        await _pending_count(session, body.student_id)
        >= MAX_PENDING_REQUESTS_PER_STUDENT
    ):
        raise SessionRequestLimitReachedError()
    if await _pending_overlaps(session, body.student_id, starts_at, ends_at):
        raise SessionRequestOverlapError()

    settings = await administration.get_business_settings(session)
    local_date = starts_at.astimezone(ZoneInfo(zone)).date()
    context = await load_family_context(
        session,
        zone=zone,
        operating_hours=_operating_days(settings),
        student_id=body.student_id,
        subject_id=body.subject_id,
        first=local_date,
        last=local_date,
    )
    if not family_slot_ok(Candidate(starts_at, ends_at), context):
        raise SlotUnavailableError()

    row = SessionRequest(
        id=uuid.uuid7(),
        student_id=body.student_id,
        subject_id=body.subject_id,
        requested_by_user_id=principal.user_id,
        starts_at=starts_at,
        ends_at=ends_at,
        duration_minutes=body.duration_minutes,
        note=body.note,
        status=SESSION_REQUEST_PENDING,
    )
    session.add(row)
    try:
        await session.flush()
    except IntegrityError as exc:
        if SESSION_REQUEST_PENDING_OVERLAP_CONSTRAINT in str(exc.orig):
            raise SessionRequestOverlapError() from None
        raise
    await administration.create_approval_request(
        session,
        request_type=SESSION_REQUEST_TYPE,
        subject_id=row.id,
        requester_user_id=principal.user_id,
    )
    return _detail(
        row,
        student=SessionRequestStudent(
            id=reference.id, display_name=reference.display_name
        ),
        subject=SessionRequestSubject(
            id=subject.id,
            name=subject.name,
            education_level_name=subject.education_level_name,
        ),
        caller_id=principal.user_id,
    )


# --- D: read and withdraw ------------------------------------------------------------


def _detail(
    row: SessionRequest,
    *,
    student: SessionRequestStudent,
    subject: SessionRequestSubject,
    caller_id: uuid.UUID,
) -> SessionRequestDetail:
    mine = row.requested_by_user_id == caller_id
    return SessionRequestDetail(
        id=row.id,
        student=student,
        subject=subject,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        duration_minutes=row.duration_minutes,
        status=row.status,  # type: ignore[arg-type]
        note=row.note,
        requested_by_me=mine,
        can_withdraw=mine and row.status == SESSION_REQUEST_PENDING,
        created_at=row.created_at,
        resolved_at=row.resolved_at,
        version=row.version,
    )


def _subject_of(
    subjects: Mapping[uuid.UUID, SubjectReference], subject_id: uuid.UUID
) -> SessionRequestSubject:
    subject = subjects.get(subject_id)
    return SessionRequestSubject(
        id=subject_id,
        name="" if subject is None else subject.name,
        education_level_name="" if subject is None else subject.education_level_name,
    )


async def list_session_requests(
    session: AsyncSession,
    principal: Principal,
    *,
    student_id: uuid.UUID | None,
    status: SessionRequestStatusFilter,
    limit: int,
    offset: int,
) -> SessionRequestPage:
    """``GET /api/v1/session-requests`` (D1): at most five statements for a
    page of any size."""
    empty = SessionRequestPage(items=[], total=0)
    if not has_family_role(principal):
        return empty
    facts = await _facts(session, principal)
    wanted = set(candidate_student_ids(principal, facts))
    if student_id is not None:
        wanted &= {student_id}
    students = await _allowed_students(session, principal, facts, wanted)
    if not students:
        return empty
    conditions: list[ColumnElement[bool]] = [
        SessionRequest.student_id.in_(list(students))
    ]
    if status != "all":
        conditions.append(SessionRequest.status == status)
    rows = (
        await session.execute(
            select(SessionRequest, func.count().over().label("total"))
            .where(*conditions)
            .order_by(SessionRequest.created_at.desc(), SessionRequest.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if rows:
        total = int(rows[0].total)
    elif offset:
        total = int(
            await session.scalar(
                select(func.count()).select_from(SessionRequest).where(*conditions)
            )
            or 0
        )
    else:
        total = 0
    requests = [row[0] for row in rows]
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in requests}
    )
    return SessionRequestPage(
        items=[
            SessionRequestItem(
                **_detail(
                    row,
                    student=SessionRequestStudent(
                        id=row.student_id,
                        display_name=students[row.student_id].display_name,
                    ),
                    subject=_subject_of(subjects, row.subject_id),
                    caller_id=principal.user_id,
                ).model_dump()
            )
            for row in requests
        ],
        total=total,
    )


async def _visible_request(
    session: AsyncSession,
    principal: Principal,
    request_id: uuid.UUID,
) -> tuple[SessionRequest, StudentAgeReference]:
    """The request if the caller may act for its student (S2), else ``404``.
    The facts are read first, so an unknown and a foreign id cost the same
    reads."""
    facts = await _facts(session, principal)
    row = await session.get(SessionRequest, request_id)
    if row is None:
        raise RecordNotFoundError()
    students = await _allowed_students(
        session,
        principal,
        facts,
        {row.student_id} & candidate_student_ids(principal, facts),
    )
    student = students.get(row.student_id)
    if student is None:
        raise RecordNotFoundError()
    return row, student


async def _detail_of(
    session: AsyncSession,
    row: SessionRequest,
    student: StudentAgeReference,
    caller_id: uuid.UUID,
) -> SessionRequestDetail:
    subjects = await catalog.subject_references(session, {row.subject_id})
    return _detail(
        row,
        student=SessionRequestStudent(id=student.id, display_name=student.display_name),
        subject=_subject_of(subjects, row.subject_id),
        caller_id=caller_id,
    )


async def get_session_request(
    session: AsyncSession, principal: Principal, request_id: uuid.UUID
) -> SessionRequestDetail:
    """``GET /api/v1/session-requests/{request_id}`` (D2)."""
    row, student = await _visible_request(session, principal, request_id)
    return await _detail_of(session, row, student, principal.user_id)


async def withdraw_session_request(
    session: AsyncSession,
    principal: Principal,
    request_id: uuid.UUID,
    *,
    version: int,
) -> SessionRequestDetail:
    """``POST /api/v1/session-requests/{request_id}/withdraw`` (D3)."""
    row, student = await _visible_request(session, principal, request_id)
    if row.requested_by_user_id != principal.user_id:
        raise PermissionDeniedError()
    locked = await session.scalar(
        select(SessionRequest)
        .where(SessionRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:  # pragma: no cover - requests are never deleted
        raise RecordNotFoundError()
    ensure_version(locked, version)
    if locked.status != SESSION_REQUEST_PENDING:
        raise SessionRequestNotPendingError()
    locked.status = SESSION_REQUEST_WITHDRAWN
    locked.resolved_at = clock.now()
    await session.flush()
    approval_id = await administration.approval_request_id_for_subject(
        session, request_type=SESSION_REQUEST_TYPE, subject_id=locked.id
    )
    if approval_id is not None:
        await administration.cancel_approval_request(
            session, request_id=approval_id, by_user_id=principal.user_id
        )
    return await _detail_of(session, locked, student, principal.user_id)


# --- A4: the time-zone dependency ----------------------------------------------------


async def has_pending_session_requests(session: AsyncSession) -> bool:
    """Whether any ``pending`` request exists (#59 A4): its instants were
    computed from the center's local time, so a zone change waits until it
    is resolved."""
    return bool(
        await session.scalar(
            select(
                select(SessionRequest.id)
                .where(SessionRequest.status == SESSION_REQUEST_PENDING)
                .exists()
            )
        )
    )
