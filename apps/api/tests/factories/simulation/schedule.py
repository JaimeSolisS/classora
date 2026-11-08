"""``schedule`` (#550 HF-01b): the timetable and its outcomes through the
scheduling, staff, approval and closeout services.

The plan (:mod:`.timetable`) is a list of dated events; this module runs
the ones that aren't the ``courses`` module's, each under
:func:`~.anchor.sim_clock` at its instant and :func:`seed_actor` of the
account that acts:

- the Administrator (``schedule.manage``, ``tutor.manage``) creates the
  weekly series and the class groups (at :data:`~.anchor.SIM_SETUP`, at a
  later student's enrollment, at tutor 12's join), decides the change and
  session requests and the time off, cancels for the center and creates
  the make-ups;
- the Academic coordinator (``schedule.assign``, ``teaching_hours.approve``,
  ``attendance.correct``) substitutes, re-offers, reviews and corrects the
  closeouts and corrects attendance;
- each tutor answers their offers, marks attendance and submits the
  closeout at the end + 15 minutes, resubmits after changes, asks for time
  off and for the pending weekly change;
- guardians (and adult students) ask for changes and sessions.

Every series is materialized through :data:`~.anchor.SIM_HORIZON` at its
creation (``horizon_weeks`` from the creation day), so the nightly job has
nothing to add (E9). The services re-check every rule at write time: a
conflict, a workload warning or an unexpected answer fails the run (one
Unit of Work, so nothing is written). The pending items are written on 17
and 18 Nov; :meth:`ScheduleModule.at_anchor` has nothing to do.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time
from typing import Any, Final, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.core.audit import AuditEvent
from app.core.authz import Principal
from app.core.errors import AppError
from app.modules.academics.models import (
    SessionCloseout,
    SessionCloseoutApproval,
    SessionCloseoutCorrection,
)
from app.modules.academics.schemas import (
    CloseoutApprove,
    CloseoutCorrection,
    CloseoutRequestChanges,
    CloseoutResubmit,
    CloseoutSummaryInput,
)
from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.billing.models import (
    BillingAccount,
    Charge,
    ChargeAdjustment,
    ChargeDiscount,
    PackageCreditTransaction,
)
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.courses.models import CohortEnrollment, CourseCohort
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.attendance_schemas import (
    AttendanceCorrectionCreate,
    AttendanceMark,
)
from app.modules.scheduling.change_request_schemas import (
    ChangeRequestApprove,
    ChangeRequestCreate,
    ChangeRequestReject,
)
from app.modules.scheduling.models import (
    AssignmentOffer,
    AssignmentOfferSession,
    ClassGroup,
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionAttendance,
    ClassSessionAttendanceCorrection,
    ClassSessionParticipant,
    ClassSessionReassignmentFlag,
    ClassSessionSubstitution,
    GroupEnrollment,
    SessionChangeRequest,
    SessionRequest,
)
from app.modules.scheduling.request_schemas import (
    SessionRequestConfirm,
    SessionRequestCreate,
    SessionRequestReject,
)
from app.modules.scheduling.schemas import (
    AssignmentOfferCreate,
    ClassSeriesCreate,
    ClassSessionCreate,
    ClassSessionUpdate,
    GroupCreate,
    SeriesRecurrence,
    SessionOverride,
    SubstitutionCreate,
)
from app.modules.staff import service as staff
from app.modules.staff.assignment_impact import registered_availability_impact_source
from app.modules.staff.models import (
    TutorAvailabilityChangeRequest,
    TutorAvailabilityException,
    TutorAvailabilitySettings,
)
from app.modules.staff.schemas import (
    AvailabilityChangeRequestItem,
    AvailabilityExceptionCreate,
    WeeklyAvailabilityUpdate,
)
from app.workflows import (
    change_request_decision,
    confirm_session_request,
    group_enrollment,
    session_closeout,
    session_closeout_review,
)
from app.workflows.session_closeout_schemas import SessionCloseoutSubmit
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import SIM_HORIZON, SIM_START, ZONE, sim_clock
from tests.factories.simulation.people import principal, user_key
from tests.factories.simulation.plan import AccountPlan
from tests.factories.simulation.timeline import SimulationContext
from tests.factories.simulation.timetable import (
    COURSE_EVENTS,
    AcceptSeries,
    AnswerOffer,
    CenterCancel,
    ChangeWeekly,
    ClassPlan,
    CorrectAttendance,
    CorrectCloseout,
    CreateGroup,
    CreateMakeup,
    CreateSeries,
    DecideChange,
    DecideSessionRequest,
    DecideTimeOff,
    Event,
    ExpireOffer,
    Hold,
    Reoffer,
    RequestChange,
    RequestSession,
    RequestTimeOff,
    Resize,
    ResubmitCloseout,
    ReviewCloseout,
    SeriesPlan,
    Substitute,
    mins,
)

SCHEDULE_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    ClassSeries,
    ClassSeriesParticipant,
    ClassSession,
    ClassSessionParticipant,
    AssignmentOffer,
    AssignmentOfferSession,
    ClassSessionSubstitution,
    ClassSessionReassignmentFlag,
    ClassGroup,
    GroupEnrollment,
    SessionRequest,
    SessionChangeRequest,
    ClassSessionAttendance,
    ClassSessionAttendanceCorrection,
    SessionCloseout,
    SessionCloseoutApproval,
    SessionCloseoutCorrection,
    TutorAvailabilityChangeRequest,
    TutorAvailabilityException,
    ApprovalRequest,
    Notification,
    # HF-01d: what the M4 hooks write in billing (package consumption at a
    # closeout approval, a later enrollment's fee) once billing is on.
    BillingAccount,
    Charge,
    ChargeDiscount,
    ChargeAdjustment,
    PackageCreditTransaction,
)
"""The tables whose rows ``schedule`` counts. ``class_session_overrides``
is left out: a confirmation's ``notice_window`` override is stored only on
the run dates where the conflict occurs (D5), so counting it would make the
totals depend on the real date."""


NOTICE_WINDOW: Final = "notice_window"


ANALYZED_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    CourseCohort,
    CohortEnrollment,
    AuditEvent,
)
"""More tables :func:`refresh_statistics` analyzes (besides the schedule's)."""


class SimulationStepError(RuntimeError):
    """A service answered something the plan doesn't expect (a warning, a
    missing offer): the plan has a bug."""


def horizon_weeks(created_on: date) -> int:
    """Enough weeks from ``created_on`` to materialize through the horizon."""
    return (SIM_HORIZON - created_on).days // 7 + 2


def local_day(at: datetime) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def no_warnings(what: str, warnings: Sequence[Any]) -> None:
    if warnings:
        raise SimulationStepError(f"{what}: {list(warnings)}")


def account(ctx: SimulationContext, username: str) -> AccountPlan:
    found = next((a for a in ctx.plan.accounts if a.username == username), None)
    if found is None:  # pragma: no cover - the plan names its own accounts
        raise SimulationStepError(f"unknown account {username}")
    return found


async def tutor_principal(
    session: AsyncSession, ctx: SimulationContext, tutor: str
) -> Principal:
    return await principal(session, ctx, ctx.plan.tutor(tutor).account)


def tutor_id(ctx: SimulationContext, tutor: str) -> uuid.UUID:
    return ctx.id(user_key(ctx.plan.tutor(tutor).account.username))


def student_id(ctx: SimulationContext, student: str) -> uuid.UUID:
    return ctx.id(f"student:{student}")


def room_id(ctx: SimulationContext, room: str | None) -> uuid.UUID | None:
    return None if room is None else ctx.id(f"classroom:{room}")


async def class_id(
    session: AsyncSession, ctx: SimulationContext, key: str
) -> uuid.UUID:
    """The session of a planned class: a one-off's id, or the series
    occurrence at its original start."""
    found = ctx.ids.get(f"class:{key}")
    if found is not None:
        return found
    c = ctx.plan.timetable.cls(key)
    if c.series is None:  # pragma: no cover - one-offs are registered at creation
        raise SimulationStepError(f"{key} isn't created yet")
    row = await session.scalar(
        select(ClassSession.id)
        .where(ClassSession.series_id == ctx.id(f"series:{c.series}"))
        .where(ClassSession.original_start == c.placed.starts_at)
    )
    if row is None:
        raise SimulationStepError(f"{key}: no occurrence at {c.placed.starts_at}")
    ctx.ids[f"class:{key}"] = row
    return row


async def class_version(session: AsyncSession, class_session_id: uuid.UUID) -> int:
    version = await session.scalar(
        select(ClassSession.version).where(ClassSession.id == class_session_id)
    )
    assert version is not None
    return version


async def closeout_version(session: AsyncSession, class_session_id: uuid.UUID) -> int:
    version = await session.scalar(
        select(SessionCloseout.version).where(
            SessionCloseout.class_session_id == class_session_id
        )
    )
    assert version is not None
    return version


async def pending_offer(
    session: AsyncSession,
    *,
    tutor: uuid.UUID,
    class_session_id: uuid.UUID | None = None,
    series_id: uuid.UUID | None = None,
) -> uuid.UUID:
    query = (
        select(AssignmentOffer.id)
        .where(AssignmentOffer.tutor_user_id == tutor)
        .where(AssignmentOffer.status == "pending")
    )
    if series_id is not None:
        query = query.where(AssignmentOffer.class_series_id == series_id)
    else:
        query = query.join(
            AssignmentOfferSession,
            AssignmentOfferSession.offer_id == AssignmentOffer.id,
        ).where(AssignmentOfferSession.class_session_id == class_session_id)
    found = await session.scalar(query)
    if found is None:
        raise SimulationStepError("no pending offer")
    return found


def pick(values: Sequence[str], key: str) -> str:
    """A deterministic choice (the same on every run and process)."""
    return values[sum(map(ord, key)) % len(values)]


def summary(ctx: SimulationContext, c: ClassPlan) -> CloseoutSummaryInput:
    texts = ctx.texts
    return CloseoutSummaryInput(
        topics=pick(texts.lists("closeout_topics")[c.subject], c.key),
        performance=pick(texts.choices("closeout_performance"), c.key),
        homework=pick(texts.choices("closeout_homework"), c.key),
        next_steps=pick(texts.choices("closeout_next_steps"), c.key),
    )


def recurrence(series: SeriesPlan) -> SeriesRecurrence:
    return SeriesRecurrence(
        weekdays=list(series.weekdays), interval=1, until=series.until
    )


async def accept_series_offer(
    session: AsyncSession, ctx: SimulationContext, owner: str, tutor: str
) -> None:
    """The tutor accepts the series offer of a series, group or cohort."""
    me = await tutor_principal(session, ctx, tutor)
    series_id = ctx.id(f"series:{owner}")
    offer = await pending_offer(session, tutor=me.user_id, series_id=series_id)
    with seed_actor(session, me.user_id):
        await scheduling.answer_offer(session, offer, caller_id=me.user_id, accept=True)


class ScheduleModule:
    name = "schedule"
    tables = SCHEDULE_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(ClassGroup.id).limit(1))) is not None

    def _events(self, ctx: SimulationContext) -> Iterable[Event]:
        return (
            e for e in ctx.plan.timetable.events if not isinstance(e, COURSE_EVENTS)
        )

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        _events_since_analyze[0] = 0  # the same points in every run
        for event in self._events(ctx):
            if local_day(event.at) < SIM_START:
                await run_event(session, ctx, event)
                await refresh_statistics(session)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {
            local_day(e.at) for e in self._events(ctx) if local_day(e.at) >= SIM_START
        }

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for event in self._events(ctx):
            if local_day(event.at) == day:
                await run_event(session, ctx, event)
                await refresh_statistics(session)
        # Nothing is read back from the identity map across days: release
        # the day's objects so flushes stay cheap (and memory flat).
        await session.flush()
        session.expunge_all()

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


ANALYZE_EVERY: Final = 100
"""Events between two :func:`refresh_statistics`."""
_events_since_analyze = [0]


async def refresh_statistics(session: AsyncSession, *, force: bool = False) -> None:
    """``ANALYZE`` the tables the run fills, every :data:`ANALYZE_EVERY`
    events. The whole run is one transaction, so autovacuum never sees its
    rows and the planner would plan the services' reads for empty tables
    (a nested loop over thousands of offers and sessions took minutes);
    ``ANALYZE`` inside the transaction samples its own rows. It writes
    statistics only, no row."""
    _events_since_analyze[0] += 1
    if not force and _events_since_analyze[0] < ANALYZE_EVERY:
        return
    _events_since_analyze[0] = 0
    names = ", ".join(
        f'"{model.__tablename__}"' for model in (*SCHEDULE_TABLES, *ANALYZED_TABLES)
    )
    await session.execute(text(f"ANALYZE {names}"))


async def run_event(
    session: AsyncSession, ctx: SimulationContext, event: Event
) -> None:
    """One planned event under the simulation clock at its instant."""
    handler = HANDLERS[type(event)]
    with sim_clock(event.at):
        try:
            await handler(session, ctx, event)
        except AppError as exc:
            raise SimulationStepError(
                f"{event}: {exc.code} {exc.conflicts or exc.errors or exc.details}"
            ) from exc


# --- Handlers -------------------------------------------------------------------------


async def _create_series(
    session: AsyncSession, ctx: SimulationContext, event: CreateSeries
) -> None:
    series = event.series
    admin = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, admin.user_id):
        created = await scheduling.create_class_series(
            session,
            ClassSeriesCreate(
                kind="individual",
                student_id=student_id(ctx, series.students[0]),
                subject_id=ctx.id(f"subject:{series.subject}"),
                tutor_user_id=tutor_id(ctx, series.tutor),
                classroom_id=room_id(ctx, series.room),
                starts_on=series.starts_on,
                start_time=time.fromisoformat(series.start),
                duration_minutes=series.minutes,
                recurrence=recurrence(series),
            ),
            horizon_weeks=horizon_weeks(local_day(event.at)),
            actor_id=admin.user_id,
        )
    no_warnings(series.key, created.warnings)
    ctx.ids[f"series:{series.key}"] = created.id


async def _create_group(
    session: AsyncSession, ctx: SimulationContext, event: CreateGroup
) -> None:
    group = event.group
    series = group.series
    admin = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, admin.user_id):
        group_id = await group_enrollment.create_group(
            session,
            GroupCreate(
                name=group.name,
                subject_id=ctx.id(f"subject:{series.subject}"),
                max_students=group.max_students,
                tutor_user_id=tutor_id(ctx, series.tutor),
                classroom_id=room_id(ctx, series.room),
                starts_on=series.starts_on,
                start_time=time.fromisoformat(series.start),
                duration_minutes=series.minutes,
                recurrence=recurrence(series),
                student_ids=[student_id(ctx, s) for s in series.students],
            ),
            horizon_weeks=horizon_weeks(local_day(event.at)),
            actor_id=admin.user_id,
        )
    ctx.ids[f"group:{group.key}"] = group_id
    found = await session.scalar(
        select(ClassSeries.id).where(ClassSeries.group_id == group_id)
    )
    assert found is not None
    ctx.ids[f"series:{group.key}"] = found
    ctx.ids[f"series:{series.key}"] = found


async def _accept_series(
    session: AsyncSession, ctx: SimulationContext, event: AcceptSeries
) -> None:
    await accept_series_offer(session, ctx, event.owner, event.tutor)


async def _hold(session: AsyncSession, ctx: SimulationContext, event: Hold) -> None:
    c = ctx.plan.timetable.cls(event.cls)
    assert c.final.tutor is not None
    me = await tutor_principal(session, ctx, c.final.tutor)
    target = await class_id(session, ctx, c.key)
    with seed_actor(session, me.user_id):
        marked = await scheduling.record_attendance(
            session,
            actor=me,
            class_session_id=target,
            version=await class_version(session, target),
            marks=[
                AttendanceMark(student_id=student_id(ctx, s), status=status)
                for s, status in c.attendance
            ],
        )
        await session_closeout.submit_session_closeout(
            session,
            principal=me,
            class_session_id=target,
            body=SessionCloseoutSubmit(
                version=marked.version,
                actual_minutes=c.final.minutes,
                summary=summary(ctx, c),
            ),
        )


async def _review(
    session: AsyncSession, ctx: SimulationContext, event: ReviewCloseout
) -> None:
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    target = await class_id(session, ctx, event.cls)
    version = await closeout_version(session, target)
    with seed_actor(session, coordinator.user_id):
        if event.approve:
            await session_closeout_review.approve_closeout(
                session,
                principal=coordinator,
                class_session_id=target,
                body=CloseoutApprove(version=version),
            )
        else:
            assert event.reason is not None
            await session_closeout_review.request_closeout_changes(
                session,
                principal=coordinator,
                class_session_id=target,
                body=CloseoutRequestChanges(version=version, reason=event.reason),
            )


async def _resubmit(
    session: AsyncSession, ctx: SimulationContext, event: ResubmitCloseout
) -> None:
    c = ctx.plan.timetable.cls(event.cls)
    assert c.final.tutor is not None
    me = await tutor_principal(session, ctx, c.final.tutor)
    target = await class_id(session, ctx, c.key)
    with seed_actor(session, me.user_id):
        await session_closeout_review.resubmit_closeout(
            session,
            principal=me,
            class_session_id=target,
            body=CloseoutResubmit(
                version=await closeout_version(session, target),
                actual_minutes=c.final.minutes,
                summary=summary(ctx, c),
            ),
        )


async def _correct_attendance(
    session: AsyncSession, ctx: SimulationContext, event: CorrectAttendance
) -> None:
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    target = await class_id(session, ctx, event.cls)
    with seed_actor(session, coordinator.user_id):
        await scheduling.correct_attendance(
            session,
            actor=coordinator,
            class_session_id=target,
            body=AttendanceCorrectionCreate(
                version=await class_version(session, target),
                student_id=student_id(ctx, event.student),
                status=event.status,
                reason=event.reason,
            ),
        )


async def _correct_closeout(
    session: AsyncSession, ctx: SimulationContext, event: CorrectCloseout
) -> None:
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    target = await class_id(session, ctx, event.cls)
    with seed_actor(session, coordinator.user_id):
        await session_closeout_review.correct_closeout(
            session,
            principal=coordinator,
            class_session_id=target,
            body=CloseoutCorrection(
                version=await closeout_version(session, target),
                actual_minutes=event.actual_minutes,
                reason=event.reason,
            ),
        )


async def _request_change(
    session: AsyncSession, ctx: SimulationContext, event: RequestChange
) -> None:
    request = event.request
    me = await principal(session, ctx, account(ctx, request.requester))
    target = await class_id(session, ctx, request.cls)
    with seed_actor(session, me.user_id):
        created = await scheduling.create_change_request(
            session,
            me,
            ChangeRequestCreate(
                class_session_id=target,
                kind=request.kind,
                proposed_starts_at=(
                    None if request.proposed is None else request.proposed.starts_at
                ),
                note=event.note,
            ),
        )
    ctx.ids[f"change:{request.key}"] = created.id


async def _decide_change(
    session: AsyncSession, ctx: SimulationContext, event: DecideChange
) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    request = next(
        r for r in ctx.plan.timetable.change_requests if r.key == event.request
    )
    request_id = ctx.id(f"change:{event.request}")
    version = await session.scalar(
        select(SessionChangeRequest.version).where(
            SessionChangeRequest.id == request_id
        )
    )
    assert version is not None
    with seed_actor(session, admin.user_id):
        if event.approve:
            fields: dict[str, Any] = {"version": version}
            if event.exception:
                fields.update(exception=True, reason=event.reason)
            if request.kind == "reschedule":
                fields["classroom_id"] = None  # the moved class has no room
            approved = await change_request_decision.approve_change_request(
                session, admin, request_id, ChangeRequestApprove(**fields)
            )
            no_warnings(event.request, approved.warnings)
        else:
            assert event.reason is not None
            await change_request_decision.reject_change_request(
                session,
                admin,
                request_id,
                ChangeRequestReject(version=version, reason=event.reason),
            )


async def _center_cancel(
    session: AsyncSession, ctx: SimulationContext, event: CenterCancel
) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    target = await class_id(session, ctx, event.cls)
    with seed_actor(session, admin.user_id):
        await scheduling.cancel_class_session(
            session,
            target,
            expected_version=await class_version(session, target),
            reason=event.reason,
            by_user_id=admin.user_id,
        )


async def _request_time_off(
    session: AsyncSession, ctx: SimulationContext, event: RequestTimeOff
) -> None:
    off = event.time_off
    me = await tutor_principal(session, ctx, off.tutor)
    with seed_actor(session, me.user_id):
        result = await staff.create_exception(
            session,
            principal=me,
            user_id=me.user_id,
            body=AvailabilityExceptionCreate(
                kind="unavailable",
                starts_on=off.day,
                ends_on=off.day,
                starts_at=off.start,
                ends_at=off.end,
                note=off.note,
            ),
            impact=registered_availability_impact_source(),
        )
    if not isinstance(result, AvailabilityChangeRequestItem):
        raise SimulationStepError(f"{off.key}: the time off affects no class")
    ctx.ids[f"availability:{off.key}"] = result.id


async def _decide_availability(
    session: AsyncSession,
    ctx: SimulationContext,
    key: str,
    approve: bool,
    reason: str | None,
) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    request_id = await session.scalar(
        select(ApprovalRequest.id).where(
            ApprovalRequest.subject_id == ctx.id(f"availability:{key}")
        )
    )
    assert request_id is not None
    with seed_actor(session, admin.user_id):
        await administration.decide_approval_request(
            session,
            principal=admin,
            request_id=request_id,
            action="approve" if approve else "reject",
            reason=reason,
        )


async def _decide_time_off(
    session: AsyncSession, ctx: SimulationContext, event: DecideTimeOff
) -> None:
    await _decide_availability(
        session, ctx, event.time_off, event.approve, event.reason
    )


async def _substitute(
    session: AsyncSession, ctx: SimulationContext, event: Substitute
) -> None:
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    target = await class_id(session, ctx, event.cls)
    with seed_actor(session, coordinator.user_id):
        created = await scheduling.substitute_class_session(
            session,
            target,
            SubstitutionCreate(
                substitute_tutor_user_id=tutor_id(ctx, event.substitute),
                version=await class_version(session, target),
                reason=event.reason,
            ),
            actor_id=coordinator.user_id,
        )
    no_warnings(event.cls, created.warnings)


async def _answer(
    session: AsyncSession, ctx: SimulationContext, event: AnswerOffer
) -> None:
    me = await tutor_principal(session, ctx, event.tutor)
    target = await class_id(session, ctx, event.cls)
    offer = await pending_offer(session, tutor=me.user_id, class_session_id=target)
    with seed_actor(session, me.user_id):
        await scheduling.answer_offer(
            session, offer, caller_id=me.user_id, accept=event.accept, note=event.note
        )


async def _expire(
    session: AsyncSession, ctx: SimulationContext, event: ExpireOffer
) -> None:
    """The ``scheduling:expire_offers`` step for the class's offer, in-process
    (E12), at its deadline."""
    target = await class_id(session, ctx, event.cls)
    found = await session.scalar(
        select(AssignmentOffer.id)
        .join(
            AssignmentOfferSession,
            AssignmentOfferSession.offer_id == AssignmentOffer.id,
        )
        .where(AssignmentOfferSession.class_session_id == target)
        .where(AssignmentOffer.status == "pending")
    )
    assert found is not None
    if not await scheduling.expire_offer(session, found, event.at):
        raise SimulationStepError(f"{event.cls}: the offer didn't expire")


async def _reoffer(
    session: AsyncSession, ctx: SimulationContext, event: Reoffer
) -> None:
    coordinator = await principal(session, ctx, ctx.plan.coordinator)
    target = await class_id(session, ctx, event.cls)
    with seed_actor(session, coordinator.user_id):
        created = await scheduling.offer_class_session(
            session,
            target,
            AssignmentOfferCreate(
                tutor_user_id=tutor_id(ctx, event.tutor),
                version=await class_version(session, target),
            ),
            actor_id=coordinator.user_id,
        )
    no_warnings(event.cls, created.warnings)


async def _request_session(
    session: AsyncSession, ctx: SimulationContext, event: RequestSession
) -> None:
    request = event.request
    me = await principal(session, ctx, account(ctx, request.requester))
    with seed_actor(session, me.user_id):
        created = await scheduling.create_session_request(
            session,
            me,
            SessionRequestCreate(
                student_id=student_id(ctx, request.student),
                subject_id=ctx.id(f"subject:{request.subject}"),
                starts_at=request.placement.starts_at,
                duration_minutes=cast(Any, request.placement.minutes),
                note=event.note,
            ),
        )
    ctx.ids[f"request:{request.key}"] = created.id


async def _decide_session_request(
    session: AsyncSession, ctx: SimulationContext, event: DecideSessionRequest
) -> None:
    """A rejection by the Administrator; a confirmation by the Owner, who
    overrides the booking notice: the confirm measures it from the request's
    database-set ``created_at``, which is the real run time (E7), so on most
    run dates a past class would break it. Listing ``notice_window`` stores an
    override only when the conflict occurs (#49 E1)."""
    request_id = ctx.id(f"request:{event.request}")
    version = await session.scalar(
        select(SessionRequest.version).where(SessionRequest.id == request_id)
    )
    assert version is not None
    if event.tutor is None:
        admin = await principal(session, ctx, ctx.plan.administrator)
        assert event.reason is not None
        with seed_actor(session, admin.user_id):
            await confirm_session_request.reject_session_request(
                session,
                admin,
                request_id,
                SessionRequestReject(version=version, reason=event.reason),
            )
        return
    assert event.cls is not None
    owner = await principal(session, ctx, ctx.plan.owner)
    with seed_actor(session, owner.user_id):
        confirmed = await confirm_session_request.confirm_session_request(
            session,
            owner,
            request_id,
            SessionRequestConfirm(
                version=version,
                tutor_user_id=tutor_id(ctx, event.tutor),
                override=SessionOverride(
                    codes=[NOTICE_WINDOW],
                    reason=pick(
                        ctx.texts.choices("notice_override_reasons"), event.request
                    ),
                ),
            ),
        )
    no_warnings(event.request, confirmed.class_session.warnings)
    ctx.ids[f"class:{event.cls}"] = confirmed.class_session.id


async def _create_makeup(
    session: AsyncSession, ctx: SimulationContext, event: CreateMakeup
) -> None:
    c = ctx.plan.timetable.cls(event.cls)
    assert c.makeup_of is not None
    admin = await principal(session, ctx, ctx.plan.administrator)
    with seed_actor(session, admin.user_id):
        created = await scheduling.create_class_session(
            session,
            ClassSessionCreate(
                kind="individual",
                student_id=student_id(ctx, c.students[0]),
                subject_id=ctx.id(f"subject:{c.subject}"),
                tutor_user_id=(
                    None if c.placed.tutor is None else tutor_id(ctx, c.placed.tutor)
                ),
                classroom_id=room_id(ctx, c.placed.room),
                starts_at=c.placed.starts_at,
                ends_at=c.placed.ends_at,
                makeup_of_session_id=await class_id(session, ctx, c.makeup_of),
            ),
            actor_id=admin.user_id,
        )
    no_warnings(c.key, created.warnings)
    ctx.ids[f"class:{c.key}"] = created.id


async def _change_weekly(
    session: AsyncSession, ctx: SimulationContext, event: ChangeWeekly
) -> None:
    """The tutor asks to drop one weekly window that has classes: a pending
    availability change request."""
    me = await tutor_principal(session, ctx, event.tutor)
    version = await session.scalar(
        select(TutorAvailabilitySettings.version).where(
            TutorAvailabilitySettings.user_id == me.user_id
        )
    )
    assert version is not None
    dropped = (event.weekday, event.start, event.end)
    windows = [w for w in ctx.plan.tutor(event.tutor).windows if w != dropped]
    with seed_actor(session, me.user_id):
        result = await staff.replace_weekly_availability(
            session,
            principal=me,
            user_id=me.user_id,
            body=WeeklyAvailabilityUpdate(
                version=version,
                windows=[
                    AvailabilityWindow(weekday=day, starts_at=s, ends_at=e)
                    for day, s, e in sorted(windows, key=lambda w: (w[0], mins(w[1])))
                ],
            ),
            impact=registered_availability_impact_source(),
        )
    if not isinstance(result, AvailabilityChangeRequestItem):
        raise SimulationStepError("the weekly change affects no class")
    ctx.ids[f"availability:weekly:{event.tutor}"] = result.id


async def _resize(session: AsyncSession, ctx: SimulationContext, event: Resize) -> None:
    """The Administrator shortens a confirmed class (same start, new end;
    HF-01d): the billing reconciliation step reprices its charge."""
    admin = await principal(session, ctx, ctx.plan.administrator)
    target = await class_id(session, ctx, event.cls)
    final = ctx.plan.timetable.cls(event.cls).final
    with seed_actor(session, admin.user_id):
        updated = await scheduling.update_class_session(
            session,
            target,
            ClassSessionUpdate(
                version=await class_version(session, target),
                starts_at=final.starts_at,
                ends_at=final.ends_at,
            ),
            actor_id=admin.user_id,
        )
    no_warnings(event.cls, updated.warnings)


HANDLERS: Final[dict[type, Any]] = {
    CreateSeries: _create_series,
    CreateGroup: _create_group,
    AcceptSeries: _accept_series,
    Hold: _hold,
    ReviewCloseout: _review,
    ResubmitCloseout: _resubmit,
    CorrectAttendance: _correct_attendance,
    CorrectCloseout: _correct_closeout,
    RequestChange: _request_change,
    DecideChange: _decide_change,
    CenterCancel: _center_cancel,
    RequestTimeOff: _request_time_off,
    DecideTimeOff: _decide_time_off,
    Substitute: _substitute,
    AnswerOffer: _answer,
    ExpireOffer: _expire,
    Reoffer: _reoffer,
    RequestSession: _request_session,
    DecideSessionRequest: _decide_session_request,
    CreateMakeup: _create_makeup,
    ChangeWeekly: _change_weekly,
    Resize: _resize,
}
"""The ``schedule`` module's event handlers (the ``courses`` events are
:mod:`.courses`')."""
