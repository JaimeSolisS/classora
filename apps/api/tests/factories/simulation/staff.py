"""``staff`` (#543, owner update 2026-11-07): full, varied tutor profiles
through the staff and approval services.

For each tutor, at :data:`~.anchor.SIM_SETUP` (tutor 12 on its join day,
right after ``people`` creates the account): the Owner writes the profile
(``professional_experience``); the tutor requests each qualification of the
plan's matrix and the Administrator approves it; the Owner sets the weekly
windows (one of four archetypes), then the limits, break and preferred
classrooms.

Dated steps: the rejected qualification (requested 8 Sep, rejected 10 Sep
with a reason), the revoked one (approved at setup, revoked by the Owner on
20 Oct with a reason, #198), the pending one (requested 18 Nov, waiting on
Approvals), and the availability exceptions, each recorded by the
Administrator a few days before it starts (four past ``unavailable``, one
partial; one past ``available``; two after the anchor). No session exists
yet, so no change becomes an availability change request (those are
HF-01b's).
"""

from collections.abc import Iterable
from datetime import date
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase

from app.modules.administration import service as administration
from app.modules.administration.models import ApprovalRequest, Notification
from app.modules.catalog.schemas import AvailabilityWindow
from app.modules.staff import service as staff
from app.modules.staff.assignment_impact import registered_availability_impact_source
from app.modules.staff.models import (
    TutorAvailabilityException,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
    TutorPreferredClassroom,
    TutorProfile,
    TutorQualification,
)
from app.modules.staff.schemas import (
    AvailabilityExceptionCreate,
    AvailabilitySettingsUpdate,
    TutorAvailabilityDetail,
    TutorUpdate,
    WeeklyAvailabilityUpdate,
)
from tests.factories.demo_clock import seed_actor
from tests.factories.simulation.anchor import ZONE, sim_clock
from tests.factories.simulation.people import principal, user_key
from tests.factories.simulation.plan import (
    ExceptionPlan,
    QualificationPlan,
    TutorPlan,
)
from tests.factories.simulation.timeline import SimulationContext

STAFF_TABLES: Final[tuple[type[DeclarativeBase], ...]] = (
    TutorProfile,
    TutorQualification,
    ApprovalRequest,
    Notification,
    TutorAvailabilitySettings,
    TutorAvailabilityWindow,
    TutorAvailabilityException,
    TutorPreferredClassroom,
)


def qualification_key(tutor: str, subject: str) -> str:
    return f"qualification:{tutor}:{subject}"


class StaffModule:
    name = "staff"
    tables = STAFF_TABLES

    async def marker(self, session: AsyncSession, ctx: SimulationContext) -> bool:
        return (await session.scalar(select(TutorProfile.id).limit(1))) is not None

    async def setup(self, session: AsyncSession, ctx: SimulationContext) -> None:
        for tutor in ctx.plan.tutors:
            if tutor.joins_at == ctx.plan.owner.created_at:
                await _set_up_tutor(session, ctx, tutor)

    def days(self, ctx: SimulationContext) -> Iterable[date]:
        return {_day(at) for at, _ in _events(ctx)}

    async def on_day(
        self, session: AsyncSession, ctx: SimulationContext, day: date
    ) -> None:
        for at, item in _events(ctx):
            if _day(at) != day:
                continue
            with sim_clock(at):
                if isinstance(item, TutorPlan):
                    await _set_up_tutor(session, ctx, item)
                elif isinstance(item, ExceptionPlan):
                    await _exception(session, ctx, item)
                else:
                    await _qualification_step(session, ctx, item, at)

    async def at_anchor(self, session: AsyncSession, ctx: SimulationContext) -> None:
        return None


def _day(at: Any) -> date:
    result: date = at.astimezone(ZONE).date()
    return result


def _events(
    ctx: SimulationContext,
) -> list[tuple[Any, TutorPlan | ExceptionPlan | QualificationPlan]]:
    plan = ctx.plan
    events: list[tuple[Any, TutorPlan | ExceptionPlan | QualificationPlan]] = []
    for tutor in plan.tutors:
        if tutor.joins_at != plan.owner.created_at:
            events.append((tutor.joins_at, tutor))
    for q in plan.qualifications:
        if q.status in ("rejected", "pending"):
            events.append((q.requested_at, q))
        if q.status in ("rejected", "revoked"):
            assert q.decided_at is not None
            events.append((q.decided_at, q))
    for e in plan.exceptions:
        events.append((e.created_at, e))
    return sorted(events, key=lambda event: event[0])


async def _set_up_tutor(
    session: AsyncSession, ctx: SimulationContext, tutor: TutorPlan
) -> None:
    plan = ctx.plan
    owner = await principal(session, ctx, plan.owner)
    tutor_id = ctx.id(user_key(tutor.account.username))

    with seed_actor(session, owner.user_id):
        await staff.update_tutor(
            session,
            principal=owner,
            user_id=tutor_id,
            body=TutorUpdate(version=0, professional_experience=tutor.experience),
        )

    revoked = [
        q.subject
        for q in plan.qualifications
        if q.status == "revoked" and q.tutor == tutor.key
    ]
    for subject in (*tutor.subjects, *revoked):
        await _request(session, ctx, tutor, subject)
        await _decide(session, ctx, tutor, subject, "approve", None)

    impact = registered_availability_impact_source()
    with seed_actor(session, owner.user_id):
        weekly = await staff.replace_weekly_availability(
            session,
            principal=owner,
            user_id=tutor_id,
            body=WeeklyAvailabilityUpdate(
                version=0,
                windows=[
                    AvailabilityWindow(weekday=day, starts_at=s, ends_at=e)
                    for day, s, e in tutor.windows
                ],
            ),
            impact=impact,
        )
        assert isinstance(weekly, TutorAvailabilityDetail)
        changes: dict[str, Any] = {}
        if tutor.daily_limit is not None:
            changes["daily_limit_minutes"] = tutor.daily_limit
        if tutor.weekly_limit is not None:
            changes["weekly_limit_minutes"] = tutor.weekly_limit
        if tutor.required_break is not None:
            changes["required_break_minutes"] = tutor.required_break
        if tutor.preferred:
            changes["preferred_classroom_ids"] = [
                ctx.id(f"classroom:{room}") for room in tutor.preferred
            ]
        if changes:
            await staff.update_availability_settings(
                session,
                principal=owner,
                user_id=tutor_id,
                body=AvailabilitySettingsUpdate(version=weekly.version, **changes),
            )


async def _request(
    session: AsyncSession, ctx: SimulationContext, tutor: TutorPlan, subject: str
) -> None:
    me = await principal(session, ctx, tutor.account)
    with seed_actor(session, me.user_id):
        item = await staff.request_qualification(
            session,
            principal=me,
            user_id=me.user_id,
            subject_id=ctx.id(f"subject:{subject}"),
        )
    ctx.ids[qualification_key(tutor.key, subject)] = item.id


async def _decide(
    session: AsyncSession,
    ctx: SimulationContext,
    tutor: TutorPlan,
    subject: str,
    action: str,
    reason: str | None,
) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    request_id = await session.scalar(
        select(ApprovalRequest.id).where(
            ApprovalRequest.subject_id == ctx.id(qualification_key(tutor.key, subject))
        )
    )
    assert request_id is not None
    with seed_actor(session, admin.user_id):
        await administration.decide_approval_request(
            session,
            principal=admin,
            request_id=request_id,
            action=action,  # type: ignore[arg-type]
            reason=reason,
        )


async def _qualification_step(
    session: AsyncSession, ctx: SimulationContext, q: QualificationPlan, at: Any
) -> None:
    tutor = ctx.plan.tutor(q.tutor)
    if q.status == "revoked":
        owner = await principal(session, ctx, ctx.plan.owner)
        key = qualification_key(q.tutor, q.subject)
        version = await session.scalar(
            select(TutorQualification.version).where(
                TutorQualification.id == ctx.id(key)
            )
        )
        assert version is not None and q.reason is not None
        with seed_actor(session, owner.user_id):
            await staff.revoke_qualification(
                session,
                principal=owner,
                user_id=ctx.id(user_key(tutor.account.username)),
                qualification_id=ctx.id(key),
                reason=q.reason,
                version=version,
            )
    elif at == q.requested_at:
        await _request(session, ctx, tutor, q.subject)
    else:
        await _decide(session, ctx, tutor, q.subject, "reject", q.reason)


async def _exception(
    session: AsyncSession, ctx: SimulationContext, e: ExceptionPlan
) -> None:
    admin = await principal(session, ctx, ctx.plan.administrator)
    tutor = ctx.plan.tutor(e.tutor)
    with seed_actor(session, admin.user_id):
        await staff.create_exception(
            session,
            principal=admin,
            user_id=ctx.id(user_key(tutor.account.username)),
            body=AvailabilityExceptionCreate(
                kind=e.kind,
                starts_on=e.starts_on,
                ends_on=e.ends_on,
                starts_at=e.starts_at,
                ends_at=e.ends_at,
                note=e.note,
            ),
            impact=registered_availability_impact_source(),
        )
