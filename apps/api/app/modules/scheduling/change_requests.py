"""Cancellation and reschedule requests by families and tutors (#62 B, D;
spec §2, §3 "user requests and admin approvals"; ADR-0005 D2, D9).

Every route is ``authenticated()`` plus the change-request record policy
(:mod:`~app.modules.scheduling.change_requests_policy`): an unknown
session, another family's session, a session families can't see and
another tutor's session all get the same ``404``; a linked guardian of an
adult without the academic consent gets ``403 student_consent_required``.
Nothing a family or tutor receives names a tutor, a room or another
participant (S3, :mod:`~app.modules.scheduling.change_request_schemas`).

**Create** (:func:`create_change_request`), after the route's ``401``,
``429`` (``change_request.user``) and the schema's ``422``:

4. the session and its participants (no lock) and the caller's facts →
   S1/S2 (``404`` / ``403``);
5. for a reschedule the zone ``FOR SHARE`` (its instants are computed in
   it), then the session ``FOR UPDATE`` (re-read);
6. ``409 change_request_kind_unsupported`` (``details.kind``) unless the
   session is ``individual`` (E2);
7. ``409 class_session_not_changeable`` (``details.state``) unless it is
   ``confirmed``; then ``409 change_request_session_started`` from its start
   on;
8. ``409 change_request_pending``, with ``details.change_request_id`` only
   when the caller may read that request;
9. a reschedule: ``422`` on ``proposed_starts_at`` for the bounds (the #48
   codes, with the session's own length), ``proposal_in_past`` (at or
   before now) and ``proposal_unchanged``; for a family the #59 slot rule
   (``family_slot_ok``, tutor-agnostic, booking notice from now) with the
   session itself left out → ``409 slot_unavailable`` (opaque). A tutor's
   proposal is checked for the bounds only (E4);
10. the classification (#61) with the policy version in force now, stored
    and never recomputed (:mod:`~app.modules.scheduling.change_request_outcome`);
11. the row (``created_at`` = now, the instant the notice is measured
    from at approval) and its ``scheduling.change_request`` inbox item,
    ``high`` priority when the session starts within 24 hours (E6);
12. ``201``.

**Lock order:** (the zone →) the session → the request (a new row) → its
inbox item. No staff notification: the inbox item is the signal.

**Read** (:func:`list_change_requests`, :func:`get_change_request`): lists
filter, details hide (``404``). **Withdraw** (:func:`withdraw_change_request`,
D3): ``404`` unless the caller made it → the request ``FOR UPDATE`` →
``409 version_conflict`` → ``409 change_request_not_pending`` (closed
body) → ``withdrawn`` and its inbox item cancelled. Lock order: the
request, then the inbox item (as reject).

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import ensure_version
from app.core.pagination import OffsetPage
from app.modules.administration import service as administration
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.families.service import StudentAccessFacts
from app.modules.scheduling.change_request_outcome import shown_outcome
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestCreate,
    ChangeRequestDecision,
    ChangeRequestDetail,
    ChangeRequestItem,
    ChangeRequestOutcome,
    ChangeRequestSession,
    ChangeRequestStatusFilter,
    ChangeRequestStudent,
    ChangeRequestSubject,
)
from app.modules.scheduling.change_requests_policy import (
    CreateOutcome,
    create_access,
    is_family_visible_state,
    is_sessions_tutor,
    may_read,
    may_withdraw,
)
from app.modules.scheduling.errors import (
    ChangeRequestKindUnsupportedError,
    ChangeRequestNotPendingError,
    ChangeRequestPendingError,
    ChangeRequestSessionStartedError,
    ClassSessionNotChangeableError,
    InvalidSessionTimesError,
    SessionValidationError,
    SlotUnavailableError,
    StudentConsentRequiredError,
)
from app.modules.scheduling.family_slots import family_slot_ok
from app.modules.scheduling.models import (
    CHANGE_REQUEST_APPROVED,
    CHANGE_REQUEST_FAMILY,
    CHANGE_REQUEST_PENDING,
    CHANGE_REQUEST_PENDING_UNIQUE,
    CHANGE_REQUEST_REJECTED,
    CHANGE_REQUEST_RESCHEDULE,
    CHANGE_REQUEST_TUTOR,
    CHANGE_REQUEST_WITHDRAWN,
    ClassSession,
    ClassSessionParticipant,
    SessionChangeRequest,
)
from app.modules.scheduling.requests import (
    _allowed_students,
    _facts,
    _operating_days,
    load_family_context,
)
from app.modules.scheduling.requests_policy import (
    FamilyAccess,
    candidate_student_ids,
    family_access,
    has_family_role,
    needs_age,
)
from app.modules.scheduling.states import SessionKind, SessionState
from app.modules.scheduling.suggestions import Candidate
from app.modules.scheduling.times import validate_session_bounds

CHANGE_REQUEST_TYPE: Final = "scheduling.change_request"
"""The approval type of a change request (#62 C6)."""

CHANGE_REQUESTS_TIME_ZONE_DEPENDENCY: Final = "scheduling.change_requests"
"""The name of the pending-reschedules check in the time-zone registry (#62
A4): their proposed instants were computed from a local choice."""

HIGH_PRIORITY_WITHIN: Final = timedelta(hours=24)
"""E6: the inbox item is ``high`` when the session starts within this."""

TUTOR_ROLE: Final = "tutor"

_MESSAGES: Final = {
    "naive_datetime": "Give the time with its UTC offset.",
    "not_whole_minutes": "Use whole minutes.",
    "ends_before_start": "The session must end after it starts.",
    "duration_out_of_range": "A session lasts 15 to 480 minutes.",
    "crosses_local_date": "A session starts and ends on the same local date.",
    "proposal_required": "Give the proposed new start.",
    "proposal_not_allowed": "A cancellation takes no proposed time.",
    "proposal_in_past": "The proposed time must be in the future.",
    "proposal_unchanged": "The proposed time is the session's current time.",
}


def _error(field: str, code: str) -> FieldError:
    return {"path": ["body", field], "code": code, "message": _MESSAGES[code]}


# --- Loading -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    """A request with the live facts of its session."""

    request: SessionChangeRequest
    starts_at: datetime
    ends_at: datetime
    subject_id: uuid.UUID
    participant_id: uuid.UUID | None


def _participant() -> Any:
    # The first participant of an individual session (it has exactly one).
    return (
        select(ClassSessionParticipant.student_id)
        .where(ClassSessionParticipant.class_session_id == ClassSession.id)
        .order_by(ClassSessionParticipant.student_id)
        .limit(1)
        .correlate(ClassSession)
        .scalar_subquery()
    )


def _rows_query() -> Any:
    return select(
        SessionChangeRequest,
        ClassSession.starts_at,
        ClassSession.ends_at,
        ClassSession.subject_id,
        _participant().label("participant_id"),
    ).join(ClassSession, ClassSession.id == SessionChangeRequest.class_session_id)


def _to_row(found: Any) -> _Row:
    return _Row(
        request=found[0],
        starts_at=found[1],
        ends_at=found[2],
        subject_id=found[3],
        participant_id=found[4],
    )


def student_of(
    request: SessionChangeRequest, participant_id: uuid.UUID | None
) -> uuid.UUID | None:
    """The student the request is about: the family's ``student_id``, or the
    session's participant for a tutor request."""
    return request.student_id if request.student_id is not None else participant_id


async def _family_outcomes(
    session: AsyncSession,
    principal: Principal,
    facts: StudentAccessFacts,
    wanted: Iterable[uuid.UUID],
) -> dict[uuid.UUID, FamilyAccess]:
    """The #59 S1 outcome of each of ``wanted`` (already limited to the
    caller's own and linked students). One statement, plus the zone only
    for a linked guardian of an adult without the consent."""
    ids = set(wanted)
    if not ids:
        return {}
    refs = await families.student_age_references(session, ids)
    today = None
    found: dict[uuid.UUID, FamilyAccess] = {}
    for student_id, ref in refs.items():
        minor = None
        if needs_age(principal, student_id, facts):
            if today is None:
                today = clock.today_in(
                    await administration.get_center_time_zone(session)
                )
            minor = families.is_minor(ref.birthdate, today)
        found[student_id] = family_access(principal, student_id, facts, is_minor=minor)
    return found


async def _names(
    session: AsyncSession, ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    refs = await families.student_references(session, set(ids))
    return {student_id: ref.display_name for student_id, ref in refs.items()}


# --- Output --------------------------------------------------------------------------


def outcome_model(
    classification: str, allowed: bool, until: datetime | None
) -> ChangeRequestOutcome:
    return ChangeRequestOutcome(
        classification=classification,  # type: ignore[arg-type]
        makeup_allowed=allowed,
        makeup_until=None if until is None else until.astimezone(UTC),
    )


def decision_model(request: SessionChangeRequest) -> ChangeRequestDecision | None:
    if request.status not in (CHANGE_REQUEST_APPROVED, CHANGE_REQUEST_REJECTED):
        return None
    assert request.resolved_at is not None  # noqa: S101 - CHECK resolved_iff_not_pending
    return ChangeRequestDecision(
        exception=request.exception,
        reason=request.decision_reason,
        decided_at=request.resolved_at,
    )


def _detail(
    row: _Row,
    *,
    subject: ChangeRequestSubject,
    student_name: str | None,
    caller_id: uuid.UUID,
) -> ChangeRequestDetail:
    request = row.request
    mine = request.requested_by_user_id == caller_id
    student_id = student_of(request, row.participant_id)
    outcome = shown_outcome(request)
    return ChangeRequestDetail(
        id=request.id,
        kind=request.kind,  # type: ignore[arg-type]
        status=request.status,  # type: ignore[arg-type]
        class_session=ChangeRequestSession(
            id=request.class_session_id,
            starts_at=row.starts_at.astimezone(UTC),
            ends_at=row.ends_at.astimezone(UTC),
            subject=subject,
        ),
        student=None
        if student_id is None
        else ChangeRequestStudent(id=student_id, display_name=student_name or ""),
        proposed_starts_at=request.proposed_starts_at,
        proposed_ends_at=request.proposed_ends_at,
        note=request.note,
        outcome=outcome_model(
            outcome.classification, outcome.makeup_allowed, outcome.makeup_until
        ),
        decision=decision_model(request),
        requested_by_me=mine,
        can_withdraw=mine and request.status == CHANGE_REQUEST_PENDING,
        created_at=request.created_at,
        resolved_at=request.resolved_at,
        version=request.version,
    )


def _subject_of(
    subjects: Mapping[uuid.UUID, Any], subject_id: uuid.UUID
) -> ChangeRequestSubject:
    subject = subjects.get(subject_id)
    return ChangeRequestSubject(
        id=subject_id,
        name="" if subject is None else subject.name,
        education_level_name="" if subject is None else subject.education_level_name,
    )


async def _details(
    session: AsyncSession,
    rows: Sequence[_Row],
    *,
    caller_id: uuid.UUID,
    known_names: Mapping[uuid.UUID, str] | None = None,
) -> list[ChangeRequestDetail]:
    """The S3 details of ``rows``: the subjects (one statement) and the
    student names not already known (at most one)."""
    if not rows:
        return []
    names = dict(known_names or {})
    missing = {
        student
        for row in rows
        if (student := student_of(row.request, row.participant_id)) is not None
        and student not in names
    }
    if missing:
        names.update(await _names(session, missing))
    subjects = await catalog.subject_references(
        session, {row.subject_id for row in rows}
    )
    return [
        _detail(
            row,
            subject=_subject_of(subjects, row.subject_id),
            student_name=names.get(student_of(row.request, row.participant_id)),  # type: ignore[arg-type]
            caller_id=caller_id,
        )
        for row in rows
    ]


# --- B: create -----------------------------------------------------------------------


async def _session_and_participants(
    session: AsyncSession, class_session_id: uuid.UUID
) -> tuple[ClassSession | None, list[uuid.UUID]]:
    """Two statements, the same for an unknown and a hidden session."""
    row: ClassSession | None = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == class_session_id)
        .execution_options(populate_existing=True)
    )
    participants = list(
        await session.scalars(
            select(ClassSessionParticipant.student_id)
            .where(ClassSessionParticipant.class_session_id == class_session_id)
            .order_by(ClassSessionParticipant.student_id)
        )
    )
    return row, participants


async def _pending_of(
    session: AsyncSession, class_session_id: uuid.UUID
) -> SessionChangeRequest | None:
    found: SessionChangeRequest | None = await session.scalar(
        select(SessionChangeRequest).where(
            SessionChangeRequest.class_session_id == class_session_id,
            SessionChangeRequest.status == CHANGE_REQUEST_PENDING,
        )
    )
    return found


def _body_errors(body: ChangeRequestCreate) -> list[FieldError]:
    """B1, with the schema's ``422`` (step 3): a reschedule needs a proposal
    and a cancellation takes none."""
    if body.kind == CHANGE_REQUEST_RESCHEDULE and body.proposed_starts_at is None:
        return [_error("proposed_starts_at", "proposal_required")]
    if body.kind != CHANGE_REQUEST_RESCHEDULE and body.proposed_starts_at is not None:
        return [_error("proposed_starts_at", "proposal_not_allowed")]
    return []


def _proposal_errors(
    body: ChangeRequestCreate, row: ClassSession, zone: str, now: datetime
) -> tuple[datetime | None, datetime | None, list[FieldError]]:
    """B2 step 9: the proposal's ``422`` codes and its instants (a
    cancellation has none)."""
    proposed = body.proposed_starts_at
    if body.kind != CHANGE_REQUEST_RESCHEDULE or proposed is None:
        return None, None, []
    if proposed.tzinfo is None or proposed.utcoffset() is None:
        return None, None, [_error("proposed_starts_at", "naive_datetime")]
    starts_at = proposed.astimezone(UTC)
    ends_at = starts_at + timedelta(minutes=row.scheduled_minutes)
    try:
        validate_session_bounds(starts_at, ends_at, zone)
    except InvalidSessionTimesError as exc:
        return None, None, [_error("proposed_starts_at", exc.reason)]
    if starts_at <= now:
        return None, None, [_error("proposed_starts_at", "proposal_in_past")]
    if starts_at == row.starts_at:
        return None, None, [_error("proposed_starts_at", "proposal_unchanged")]
    return starts_at, ends_at, []


async def create_change_request(
    session: AsyncSession, principal: Principal, body: ChangeRequestCreate
) -> ChangeRequestDetail:
    """``POST /api/v1/change-requests``: see the module docstring."""
    errors = _body_errors(body)
    if errors:
        raise SessionValidationError(errors)
    facts = await _facts(session, principal)
    found, participants = await _session_and_participants(
        session, body.class_session_id
    )
    if found is None:
        raise RecordNotFoundError()
    outcomes: dict[uuid.UUID, FamilyAccess] = {}
    if not is_sessions_tutor(
        principal, found.tutor_user_id
    ) and is_family_visible_state(found.state):
        outcomes = await _family_outcomes(
            session,
            principal,
            facts,
            set(participants) & candidate_student_ids(principal, facts),
        )
    access = create_access(
        principal, state=found.state, tutor_user_id=found.tutor_user_id, family=outcomes
    )
    if access.outcome is CreateOutcome.CONSENT_REQUIRED:
        raise StudentConsentRequiredError()
    if access.outcome is not CreateOutcome.ALLOWED or access.initiated_by is None:
        raise RecordNotFoundError()
    allowed_students = {
        student
        for student, outcome in outcomes.items()
        if outcome is FamilyAccess.ALLOWED
    }

    zone = None
    if body.kind == CHANGE_REQUEST_RESCHEDULE:
        zone = await administration.get_center_time_zone(session, for_share=True)
    row = await session.scalar(
        select(ClassSession)
        .where(ClassSession.id == found.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:  # pragma: no cover - sessions are never deleted
        raise RecordNotFoundError()
    if row.kind != SessionKind.INDIVIDUAL:
        raise ChangeRequestKindUnsupportedError(row.kind)
    if row.state != SessionState.CONFIRMED:
        raise ClassSessionNotChangeableError(row.state)
    now = clock.now()
    if row.starts_at <= now:
        raise ChangeRequestSessionStartedError()

    pending = await _pending_of(session, row.id)
    if pending is not None:
        readable = may_read(
            principal,
            initiated_by=pending.initiated_by,
            requested_by_user_id=pending.requested_by_user_id,
            student_id=pending.student_id,
            family_students=allowed_students,
        )
        raise ChangeRequestPendingError(str(pending.id) if readable else None)

    proposed_starts_at, proposed_ends_at, errors = _proposal_errors(
        body, row, zone or "UTC", now
    )
    if errors:
        raise SessionValidationError(errors)
    if (
        access.initiated_by == CHANGE_REQUEST_FAMILY
        and proposed_starts_at is not None
        and proposed_ends_at is not None
        and zone is not None
        and access.student_id is not None
    ):
        settings = await administration.get_business_settings(session)
        local_date = proposed_starts_at.astimezone(ZoneInfo(zone)).date()
        context = await load_family_context(
            session,
            zone=zone,
            operating_hours=_operating_days(settings),
            student_id=access.student_id,
            subject_id=row.subject_id,
            first=local_date,
            last=local_date,
        )
        if not family_slot_ok(
            Candidate(proposed_starts_at, proposed_ends_at),
            context,
            exclude_session_ids=frozenset({row.id}),
        ):
            raise SlotUnavailableError()

    policy = await administration.get_scheduling_policy(session, now)
    outcome = administration.classify_change(
        policy,
        kind="reschedule" if body.kind == CHANGE_REQUEST_RESCHEDULE else "cancellation",
        initiated_by=access.initiated_by,
        starts_at=row.starts_at,
        at=now,
    )
    request = SessionChangeRequest(
        id=uuid.uuid7(),
        class_session_id=row.id,
        kind=body.kind,
        initiated_by=access.initiated_by,
        requested_by_user_id=principal.user_id,
        student_id=access.student_id,
        note=body.note,
        session_starts_at=row.starts_at,
        session_ends_at=row.ends_at,
        proposed_starts_at=proposed_starts_at,
        proposed_ends_at=proposed_ends_at,
        policy_id=outcome.policy_id,
        classification=outcome.classification,
        makeup_allowed=outcome.makeup_allowed,
        makeup_until=outcome.makeup_until,
        status=CHANGE_REQUEST_PENDING,
        exception=False,
    )
    request.created_at = now
    session.add(request)
    try:
        await session.flush()
    except IntegrityError as exc:
        if CHANGE_REQUEST_PENDING_UNIQUE in str(exc.orig):
            raise ChangeRequestPendingError() from None
        raise
    await administration.create_approval_request(
        session,
        request_type=CHANGE_REQUEST_TYPE,
        subject_id=request.id,
        requester_user_id=principal.user_id,
        priority="high" if row.starts_at - now < HIGH_PRIORITY_WITHIN else "normal",
    )
    [detail] = await _details(
        session,
        [
            _Row(
                request=request,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                subject_id=row.subject_id,
                participant_id=participants[0] if participants else None,
            )
        ],
        caller_id=principal.user_id,
    )
    return detail


# --- D: read and withdraw ------------------------------------------------------------


async def _family_students(
    session: AsyncSession,
    principal: Principal,
    facts: StudentAccessFacts,
    wanted: Iterable[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Of ``wanted`` ∩ the caller's own and linked students, those the caller
    may act for (S1), with their names."""
    candidates = set(wanted) & candidate_student_ids(principal, facts)
    allowed = await _allowed_students(session, principal, facts, candidates)
    return {student_id: ref.display_name for student_id, ref in allowed.items()}


async def list_change_requests(
    session: AsyncSession,
    principal: Principal,
    *,
    student_id: uuid.UUID | None,
    class_session_id: uuid.UUID | None,
    status: ChangeRequestStatusFilter,
    limit: int,
    offset: int,
) -> OffsetPage[ChangeRequestItem]:
    """``GET /api/v1/change-requests`` (D1): the caller's visible requests,
    newest first; at most five statements for a page of any size."""
    empty = OffsetPage[ChangeRequestItem](items=[], total=0, limit=limit, offset=offset)
    is_tutor = TUTOR_ROLE in principal.roles
    family: dict[uuid.UUID, str] = {}
    if has_family_role(principal):
        facts = await _facts(session, principal)
        wanted = candidate_student_ids(principal, facts)
        if student_id is not None:
            wanted &= {student_id}
        family = await _family_students(session, principal, facts, wanted)
    visible: list[ColumnElement[bool]] = []
    if family:
        visible.append(
            and_(
                SessionChangeRequest.initiated_by == CHANGE_REQUEST_FAMILY,
                SessionChangeRequest.student_id.in_(list(family)),
            )
        )
    if is_tutor:
        mine = and_(
            SessionChangeRequest.initiated_by == CHANGE_REQUEST_TUTOR,
            SessionChangeRequest.requested_by_user_id == principal.user_id,
        )
        if student_id is not None:
            mine = and_(
                mine,
                exists().where(
                    ClassSessionParticipant.class_session_id
                    == SessionChangeRequest.class_session_id,
                    ClassSessionParticipant.student_id == student_id,
                ),
            )
        visible.append(mine)
    if not visible:
        return empty
    conditions: list[ColumnElement[bool]] = [or_(*visible)]
    if class_session_id is not None:
        conditions.append(SessionChangeRequest.class_session_id == class_session_id)
    if status != "all":
        conditions.append(SessionChangeRequest.status == status)
    found = (
        await session.execute(
            _rows_query()
            .add_columns(func.count().over().label("total"))
            .where(*conditions)
            .order_by(
                SessionChangeRequest.created_at.desc(), SessionChangeRequest.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    ).all()
    if found:
        total = int(found[0].total)
    elif offset:
        total = int(
            await session.scalar(
                select(func.count())
                .select_from(SessionChangeRequest)
                .where(*conditions)
            )
            or 0
        )
    else:
        total = 0
    details = await _details(
        session,
        [_to_row(item) for item in found],
        caller_id=principal.user_id,
        known_names=family,
    )
    return OffsetPage[ChangeRequestItem](
        items=[ChangeRequestItem(**detail.model_dump()) for detail in details],
        total=total,
        limit=limit,
        offset=offset,
    )


async def _visible(
    session: AsyncSession, principal: Principal, change_request_id: uuid.UUID
) -> tuple[_Row, dict[uuid.UUID, str]]:
    """The request if the caller may read it (S2), else ``404``. The facts
    are read first, so an unknown and a hidden id cost the same reads."""
    facts = await _facts(session, principal)
    found = (
        await session.execute(
            _rows_query()
            .where(SessionChangeRequest.id == change_request_id)
            .execution_options(populate_existing=True)
        )
    ).first()
    if found is None:
        raise RecordNotFoundError()
    row = _to_row(found)
    request = row.request
    family: dict[uuid.UUID, str] = {}
    if request.initiated_by == CHANGE_REQUEST_FAMILY and request.student_id is not None:
        family = await _family_students(session, principal, facts, {request.student_id})
    if not may_read(
        principal,
        initiated_by=request.initiated_by,
        requested_by_user_id=request.requested_by_user_id,
        student_id=request.student_id,
        family_students=family,
    ):
        raise RecordNotFoundError()
    return row, family


async def get_change_request(
    session: AsyncSession, principal: Principal, change_request_id: uuid.UUID
) -> ChangeRequestDetail:
    """``GET /api/v1/change-requests/{change_request_id}`` (D2)."""
    row, family = await _visible(session, principal, change_request_id)
    [detail] = await _details(
        session, [row], caller_id=principal.user_id, known_names=family
    )
    return detail


async def withdraw_change_request(
    session: AsyncSession,
    principal: Principal,
    change_request_id: uuid.UUID,
    *,
    version: int,
) -> ChangeRequestDetail:
    """``POST /api/v1/change-requests/{change_request_id}/withdraw`` (D3)."""
    row, family = await _visible(session, principal, change_request_id)
    if not may_withdraw(
        principal, requested_by_user_id=row.request.requested_by_user_id
    ):
        raise RecordNotFoundError()
    locked = await session.scalar(
        select(SessionChangeRequest)
        .where(SessionChangeRequest.id == change_request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked is None:  # pragma: no cover - requests are never deleted
        raise RecordNotFoundError()
    ensure_version(locked, version)
    if locked.status != CHANGE_REQUEST_PENDING:
        raise ChangeRequestNotPendingError()
    locked.status = CHANGE_REQUEST_WITHDRAWN
    locked.resolved_at = clock.now()
    await session.flush()
    approval_id = await administration.approval_request_id_for_subject(
        session, request_type=CHANGE_REQUEST_TYPE, subject_id=locked.id
    )
    if approval_id is not None:
        await administration.cancel_approval_request(
            session, request_id=approval_id, by_user_id=principal.user_id
        )
    [detail] = await _details(
        session,
        [
            _Row(
                request=locked,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                subject_id=row.subject_id,
                participant_id=row.participant_id,
            )
        ],
        caller_id=principal.user_id,
        known_names=family,
    )
    return detail


# --- Hooks for later milestones and the time-zone dependency -------------------------


@dataclass(frozen=True)
class ChangeRequestOutcomeView:
    """What M6-14 (billing) and M7-04 (tutor pay) read about a request
    (:func:`get_change_request_outcome`)."""

    change_request_id: uuid.UUID
    class_session_id: uuid.UUID
    kind: str
    initiated_by: str
    status: str
    exception: bool
    classification: str
    """The effective classification once ``approved``; otherwise the
    request-time one ("as requested": no money effect)."""
    makeup_allowed: bool
    makeup_until: datetime | None
    policy_id: uuid.UUID
    session_starts_at: datetime
    """The session's start when the request was made."""


async def get_change_request_outcome(
    session: AsyncSession, change_request_id: uuid.UUID
) -> ChangeRequestOutcomeView | None:
    """The outcome of one request (``None`` for an unknown id; one
    statement). Only an ``approved`` request has a money effect; M6-14 and
    M7-04 receive its id from the cancellation and reschedule seams."""
    request = await session.get(SessionChangeRequest, change_request_id)
    if request is None:
        return None
    outcome = shown_outcome(request)
    return ChangeRequestOutcomeView(
        change_request_id=request.id,
        class_session_id=request.class_session_id,
        kind=request.kind,
        initiated_by=request.initiated_by,
        status=request.status,
        exception=request.exception,
        classification=outcome.classification,
        makeup_allowed=outcome.makeup_allowed,
        makeup_until=outcome.makeup_until,
        policy_id=request.policy_id,
        session_starts_at=request.session_starts_at,
    )


async def has_pending_reschedule_requests(session: AsyncSession) -> bool:
    """Whether a ``pending`` reschedule request exists (#62 A4): its proposed
    instants were computed from the center's local time."""
    return bool(
        await session.scalar(
            select(
                select(SessionChangeRequest.id)
                .where(
                    SessionChangeRequest.status == CHANGE_REQUEST_PENDING,
                    SessionChangeRequest.kind == CHANGE_REQUEST_RESCHEDULE,
                )
                .exists()
            )
        )
    )
