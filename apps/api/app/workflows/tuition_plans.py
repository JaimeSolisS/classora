"""Tuition plans (#93 D; K3-K9): the cross-module reads and writes behind
``app.workflows.tuition_plans_router``.

The billing module owns the plan and pause rows
(``app.modules.billing.service``: ``tuition_plans.py``) and the period grid
(``tuition.py``). This workflow adds what needs other modules (ADR-0002):
the student and family (``families.service``), the linked group enrollment
and its group (``scheduling.service``), the subject names (``catalog``),
the center's local today (``administration``), and the #89 item summary
(``app.workflows.price_catalog``).

**Create order** (D3), after the body's ``422``: the student
(``student_unknown``, ``student_inactive``) → the pinned item
(``tuition_plan_price_invalid``: unknown, not ``per_period``, ``individual``
with an enrollment, ``group`` without one) → the enrollment
(``tuition_plan_enrollment_invalid``: unknown, another student's, withdrawn)
→ the item's scope (a ``group`` item scoped to the enrollment's group, or
unscoped with its subject; else ``tuition_plan_price_invalid``) →
``starts_on`` (31 days back to 366 ahead of the center's today, not before
the enrollment's local date: ``tuition_plan_starts_on_invalid``) →
``ends_on`` (a period end: ``tuition_plan_ends_on_invalid``) → the family
(``409 student_has_no_family``, K9) → **locks**: (linked) the group row
``FOR SHARE`` and the enrollment re-read → the family's account
(``ensure_billing_account``) ``FOR UPDATE`` → ``409 tuition_plan_overlap``
→ (linked, K6) ``409 tuition_plan_enrollment_fee_charged`` (the period's
flat fee has a net above ``0.00``) and ``409 tuition_plan_sessions_charged``
(a ``session`` charge of the student with a net above ``0.00`` on a group
session of the period dated inside the plan's window) → insert. The #91
session step and the #92 fee step re-check ``tuition_priced`` under the same
account lock, so whichever commits first wins.

**Plan writes** (``PATCH``, ``DELETE``, pauses, resume) read the plan, take
(linked) the group ``FOR SHARE`` then the plan row ``FOR UPDATE`` (the
daily job's order) and check ``version`` (``409 version_conflict``).

**Status** (D1, for the center's today): ``ended`` (the linked enrollment
was withdrawn on or before today, or ``ends_on`` is before the start of
today's period) → ``scheduled`` (``starts_on`` after today) → ``paused``
(today's period starts inside a pause) → ``active``.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import FieldError
from app.core.models import StaleVersionError
from app.core.pagination import OffsetPage, OffsetParams
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import floor_instant, local_date
from app.workflows.price_catalog import catalog_names, service_price_item
from app.workflows.tuition_charges import (
    PlanContext,
    period_outcomes,
    plan_context,
    price_query,
    withdrawn_on,
)
from app.workflows.tuition_plan_schemas import (
    EligibleEnrollment,
    TuitionPauseCreate,
    TuitionPauseItem,
    TuitionPlanCreate,
    TuitionPlanDetail,
    TuitionPlanItem,
    TuitionPlanStatus,
    TuitionPlanUpdate,
    TuitionPostedPeriod,
    TuitionRecord,
    TuitionResume,
    TuitionUnbilledReason,
    TuitionUpcomingPeriod,
)

STARTS_ON_PAST_DAYS: Final = 31
STARTS_ON_FUTURE_DAYS: Final = 366
"""The ``starts_on`` window around the center's today (K5)."""
RECENT_PERIODS: Final = 24
UPCOMING_PERIODS: Final = 12
"""The detail's posted and upcoming periods (D1)."""


# --- Status and names ----------------------------------------------------------------


def today_period_start(plan: billing.TuitionPlanRow, today: date) -> date | None:
    """The start of the plan's period that contains ``today`` (``None``
    before ``starts_on``)."""
    found = billing.period_containing(plan.starts_on, plan.frequency, today)
    return None if found is None else found.start


def has_ended(
    plan: billing.TuitionPlanRow, today: date, withdrawn: date | None = None
) -> bool:
    """D1/D5: the linked enrollment was withdrawn on or before ``today``, or
    ``ends_on`` is before the start of today's period."""
    if withdrawn is not None and withdrawn <= today:
        return True
    current = today_period_start(plan, today)
    return plan.ends_on is not None and current is not None and plan.ends_on < current


def plan_status(
    plan: billing.TuitionPlanRow,
    pauses: Sequence[tuple[date, date | None]],
    today: date,
    withdrawn: date | None,
) -> TuitionPlanStatus:
    """The D1 status (the order of the module docstring)."""
    if has_ended(plan, today, withdrawn):
        return "ended"
    current = today_period_start(plan, today)
    if current is None:
        return "scheduled"
    if billing.in_pause(current, pauses):
        return "paused"
    return "active"


@dataclass(frozen=True)
class _Names:
    students: Mapping[uuid.UUID, str]
    families: Mapping[uuid.UUID, str]


async def _executed(
    session: AsyncSession, columns: Any, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    query = columns.where(columns.selected_columns[0].in_(set(ids)))
    return {row[0]: row[1] for row in await session.execute(query)}


async def _names(
    session: AsyncSession, plans: Sequence[billing.TuitionPlanRow]
) -> _Names:
    students = await families.student_references(session, {p.student_id for p in plans})
    family_names = await _executed(
        session, families.family_name_columns(), {p.family_id for p in plans}
    )
    return _Names(
        students={sid: ref.display_name for sid, ref in students.items()},
        families=family_names,
    )


async def _items(
    session: AsyncSession, plans: Sequence[billing.TuitionPlanRow], today: date
) -> tuple[dict[uuid.UUID, billing.CatalogItemRow], dict[uuid.UUID, Any]]:
    ids = {p.service_price_id for p in plans}
    rows = await billing.get_service_prices(session, catalog_names(), ids)
    versions = await billing.current_and_next(session, list(rows), today=today)
    return rows, versions


def _item(
    plan: billing.TuitionPlanRow,
    context: PlanContext,
    rows: Mapping[uuid.UUID, billing.CatalogItemRow],
    versions: Mapping[uuid.UUID, billing.CurrentAndNext],
    names: _Names,
    today: date,
) -> TuitionPlanItem:
    facts = context.facts[plan.id]
    row = rows[plan.service_price_id]
    current = versions[plan.service_price_id]
    named = (
        None
        if plan.group_enrollment_id is None
        else context.enrollments.get(plan.group_enrollment_id)
    )
    return TuitionPlanItem(
        id=plan.id,
        version=plan.version,
        student=TuitionRecord(
            id=plan.student_id, name=names.students.get(plan.student_id, "")
        ),
        family=TuitionRecord(
            id=plan.family_id, name=names.families.get(plan.family_id, "")
        ),
        service_price=service_price_item(row, current),
        group=None
        if named is None
        else TuitionRecord(id=named.period.group_id, name=named.group_name),
        group_enrollment_id=plan.group_enrollment_id,
        frequency=plan.frequency,
        starts_on=plan.starts_on,
        ends_on=plan.ends_on,
        due_offset_days=plan.due_offset_days,
        status=plan_status(plan, facts.pauses, today, facts.withdrawn_on),
        current_amount=None if current.current is None else current.current.amount,
    )


# --- Reads ---------------------------------------------------------------------------


async def list_tuition_plans(
    session: AsyncSession,
    *,
    student_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
    group_id: uuid.UUID | None,
    statuses: Collection[TuitionPlanStatus],
    page: OffsetParams,
) -> OffsetPage[TuitionPlanItem]:
    """``GET /api/v1/billing/tuition-plans`` (D2): the plans matching the
    filters (the status is derived for the center's today, so the filters
    and the order are applied to the matching plans in memory), by student
    name, ``starts_on`` descending and id. At most ten statements whatever
    the page size."""
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    plans = await billing.list_plans(
        session, student_id=student_id, family_id=family_id
    )
    context = await plan_context(session, plans, zone=zone)
    if group_id is not None:
        plans = [
            p
            for p in plans
            if p.group_enrollment_id is not None
            and (named := context.enrollments.get(p.group_enrollment_id)) is not None
            and named.period.group_id == group_id
        ]
    if statuses:
        wanted = set(statuses)
        plans = [
            p
            for p in plans
            if plan_status(
                p,
                context.facts[p.id].pauses,
                today,
                context.facts[p.id].withdrawn_on,
            )
            in wanted
        ]
    names = await _names(session, plans)
    plans.sort(
        key=lambda p: (
            names.students.get(p.student_id, "").casefold(),
            -p.starts_on.toordinal(),
            p.id,
        )
    )
    window = plans[page.offset : page.offset + page.limit]
    rows, versions = await _items(session, window, today)
    return OffsetPage[TuitionPlanItem](
        items=[_item(p, context, rows, versions, names, today) for p in window],
        total=len(plans),
        limit=page.limit,
        offset=page.offset,
    )


async def _upcoming(
    session: AsyncSession,
    plan: billing.TuitionPlanRow,
    context: PlanContext,
    *,
    today: date,
    floor: date | None,
) -> list[TuitionUpcomingPeriod]:
    facts = context.facts[plan.id]
    start = today_period_start(plan, today) or plan.starts_on
    k = billing.period_index(plan.starts_on, plan.frequency, start) or 0
    periods = [
        billing.period_at(plan.starts_on, plan.frequency, k + i)
        for i in range(UPCOMING_PERIODS)
    ]
    if plan.ends_on is not None:
        periods = [p for p in periods if p.start <= plan.ends_on]
    reasons: dict[date, TuitionUnbilledReason] = {}
    if floor is not None and periods:
        outcomes = period_outcomes(
            facts, first=periods[0].start, today=today, floor=floor
        )
        pending = [period for period, outcome in outcomes if outcome == "priceable"]
        for period, outcome in outcomes:
            if outcome == "family_changed":
                reasons[period.start] = "family_changed"
        if pending:
            item = await billing.per_period_item(session, plan.service_price_id)
            assert item is not None  # noqa: S101 - RESTRICT keeps the item
            queries = {
                period.start: price_query(plan, item, period, education_level_id=None)
                for period in pending
            }
            prices = await billing.pinned_base_prices(session, list(queries.values()))
            for start_on, query in queries.items():
                if prices.get(query) is None:
                    reasons[start_on] = "no_price"
    charges = {
        p.period_start: p.charge_id
        for p in await billing.recent_periods(
            session, plan.id, today=today, limit=len(periods) + RECENT_PERIODS
        )
    }
    return [
        TuitionUpcomingPeriod(
            period_start=period.start,
            period_end=period.end,
            state=billing.plan_period_state(
                plan.terms,
                facts.pauses,
                period,
                today=today,
                floor=floor,
                enrollment=facts.withdrawn_on,
            ),
            charge_id=charges.get(period.start),
            reason=reasons.get(period.start),
        )
        for period in periods
    ]


async def get_tuition_plan(
    session: AsyncSession, plan_id: uuid.UUID
) -> TuitionPlanDetail:
    """``GET /api/v1/billing/tuition-plans/{id}`` (D4): ``404 not_found``
    for an unknown id."""
    plan = await billing.get_plan(session, plan_id)
    if plan is None:
        raise RecordNotFoundError()
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    context = await plan_context(session, [plan], zone=zone)
    names = await _names(session, [plan])
    rows, versions = await _items(session, [plan], today)
    item = _item(plan, context, rows, versions, names, today)
    pauses = (await billing.plan_pauses(session, [plan.id])).get(plan.id, [])
    floor = await billing.billing_starts_on(session)
    recent = await billing.recent_periods(
        session, plan.id, today=today, limit=RECENT_PERIODS
    )
    through = (await billing.posted_through(session, [plan.id])).get(plan.id)
    return TuitionPlanDetail(
        **item.model_dump(),
        pauses=[
            TuitionPauseItem(
                id=p.id, version=p.version, starts_on=p.starts_on, ends_on=p.ends_on
            )
            for p in pauses
        ],
        posted_through=through,
        recent_periods=[
            TuitionPostedPeriod(
                period_start=p.period_start,
                period_end=p.period_end,
                charge_id=p.charge_id,
                amount=p.amount,
                status=p.status,
            )
            for p in recent
        ],
        upcoming_periods=await _upcoming(
            session, plan, context, today=today, floor=floor
        ),
    )


async def eligible_enrollments(
    session: AsyncSession, student_id: uuid.UUID
) -> list[EligibleEnrollment]:
    """``GET /api/v1/billing/tuition-plans/eligible-enrollments`` (D9): the
    student's active group enrollments, oldest first, with their group and
    subject and whether each has a flat fee charge or a linked plan. Five
    statements at most."""
    zone = await administration.get_center_time_zone(session)
    periods = await scheduling.named_group_periods(
        session, student_id=student_id, active_only=True
    )
    if not periods:
        return []
    ids = set(periods)
    fees = await billing.group_fee_enrollment_ids(session, ids)
    linked = {
        p.group_enrollment_id
        for p in await billing.list_plans(session, group_enrollment_ids=ids)
    }
    subjects = await _executed(
        session,
        catalog.subject_name_columns(),
        {named.period.subject_id for named in periods.values()},
    )
    return [
        EligibleEnrollment(
            group_enrollment_id=enrollment_id,
            group=TuitionRecord(id=named.period.group_id, name=named.group_name),
            subject=TuitionRecord(
                id=named.period.subject_id,
                name=subjects.get(named.period.subject_id, ""),
            ),
            enrolled_on=local_date(named.period.enrolled_at, zone),
            has_fee_charge=enrollment_id in fees,
            has_plan=enrollment_id in linked,
        )
        for enrollment_id, named in periods.items()
    ]


# --- Create (D3) ---------------------------------------------------------------------


def _student_error(code: str, message: str) -> billing.BillingValidationError:
    error: FieldError = {
        "path": ["body", "student_id"],
        "code": code,
        "message": message,
    }
    return billing.BillingValidationError([error])


async def _sessions_charged(
    session: AsyncSession,
    period: scheduling.GroupPeriod,
    *,
    zone: str,
    window_start: date,
    window_end: date | None,
) -> bool:
    """K6: a ``session`` charge of the student with a net above ``0.00`` on
    a group session of the period whose local date lies in the window."""
    start = max(period.enrolled_at, floor_instant(window_start, zone))
    seats = await scheduling.group_sessions_after(
        session, period.group_id, period.student_id, start - timedelta(microseconds=1)
    )
    end = (
        None
        if window_end is None
        else floor_instant(window_end + timedelta(days=1), zone)
    )
    inside = [
        seat.class_session_id
        for seat in seats
        if (end is None or seat.starts_at < end)
        and (period.withdrawn_at is None or seat.starts_at < period.withdrawn_at)
    ]
    return await billing.positive_session_charges(session, period.student_id, inside)


async def _check_link(
    session: AsyncSession,
    period: scheduling.GroupPeriod,
    *,
    zone: str,
    window_start: date,
    window_end: date | None,
) -> None:
    """The K6 refusals, under the account lock."""
    fee = await billing.group_fee_net(session, period.enrollment_id)
    if fee is not None and fee.is_positive():
        raise billing.TuitionPlanEnrollmentFeeChargedError()
    if await _sessions_charged(
        session, period, zone=zone, window_start=window_start, window_end=window_end
    ):
        raise billing.TuitionPlanSessionsChargedError()


async def create_tuition_plan(
    session: AsyncSession, body: TuitionPlanCreate
) -> tuple[TuitionPlanDetail, bool]:
    """``POST /api/v1/billing/tuition-plans`` (D3; the order of the module
    docstring). Returns the detail and whether a period may be due now (the
    router defers the job after the commit)."""
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    student = (await families.student_references(session, [body.student_id])).get(
        body.student_id
    )
    if student is None:
        raise _student_error("student_unknown", "Choose an existing student.")
    if not student.is_active:
        raise _student_error("student_inactive", "The student is inactive.")
    item = await billing.per_period_item(session, body.service_price_id)
    linked = body.group_enrollment_id is not None
    if (
        item is None
        or item.pricing_model != "per_period"
        or item.class_type != ("group" if linked else "individual")
    ):
        raise billing.TuitionPlanPriceInvalidError()
    period: scheduling.GroupPeriod | None = None
    if body.group_enrollment_id is not None:
        period = await _enrollment(session, body.group_enrollment_id, body.student_id)
        if not (
            item.group_id == period.group_id
            or (item.group_id is None and item.subject_id == period.subject_id)
        ):
            raise billing.TuitionPlanPriceInvalidError()
    frequency = cast(billing.Frequency, item.period_frequency)
    if not (
        today - timedelta(days=STARTS_ON_PAST_DAYS)
        <= body.starts_on
        <= today + timedelta(days=STARTS_ON_FUTURE_DAYS)
    ) or (period is not None and body.starts_on < local_date(period.enrolled_at, zone)):
        raise billing.TuitionPlanStartsOnInvalidError()
    if body.ends_on is not None and not billing.is_period_end(
        body.starts_on, frequency, body.ends_on
    ):
        raise billing.TuitionPlanEndsOnInvalidError()
    family = await families.family_of_student(session, body.student_id)
    if family is None:
        raise billing.StudentHasNoFamilyAccountError()
    if period is not None:
        await scheduling.lock_group_for_billing(session, period.group_id)
        period = await _enrollment(session, period.enrollment_id, body.student_id)
    account_id = await billing.ensure_billing_account(session, family.id)
    await billing.lock_billing_account(session, account_id)
    if await billing.plan_overlaps(
        session,
        student_id=body.student_id,
        service_price_id=item.id,
        group_enrollment_id=body.group_enrollment_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
    ):
        raise billing.TuitionPlanOverlapError()
    if period is not None:
        await _check_link(
            session,
            period,
            zone=zone,
            window_start=body.starts_on,
            window_end=body.ends_on,
        )
    plan_id = await billing.create_plan(
        session,
        billing_account_id=account_id,
        student_id=body.student_id,
        service_price_id=item.id,
        group_enrollment_id=body.group_enrollment_id,
        frequency=frequency,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        due_offset_days=body.due_offset_days,
    )
    return await get_tuition_plan(session, plan_id), body.starts_on <= today


async def _enrollment(
    session: AsyncSession, enrollment_id: uuid.UUID, student_id: uuid.UUID
) -> scheduling.GroupPeriod:
    found = (
        await scheduling.named_group_periods(session, enrollment_ids=[enrollment_id])
    ).get(enrollment_id)
    if (
        found is None
        or found.period.student_id != student_id
        or found.period.withdrawn_at is not None
    ):
        raise billing.TuitionPlanEnrollmentInvalidError()
    return found.period


# --- Plan writes (D5-D8) -------------------------------------------------------------


@dataclass(frozen=True)
class _Locked:
    plan: billing.TuitionPlanRow
    period: scheduling.GroupPeriod | None
    zone: str
    today: date


async def _lock_plan(
    session: AsyncSession, plan_id: uuid.UUID, version: int
) -> _Locked:
    """The plan (``404``) → (linked) the group ``FOR SHARE`` → the plan row
    ``FOR UPDATE`` → ``409 version_conflict``."""
    plan = await billing.get_plan(session, plan_id)
    if plan is None:
        raise RecordNotFoundError()
    if plan.group_enrollment_id is not None:
        found = (
            await scheduling.named_group_periods(
                session, enrollment_ids=[plan.group_enrollment_id]
            )
        ).get(plan.group_enrollment_id)
        if found is not None:
            await scheduling.lock_group_for_billing(session, found.period.group_id)
    locked = await billing.get_plan(session, plan_id, lock="update")
    if locked is None:  # pragma: no cover - deleted while waiting
        raise RecordNotFoundError()
    if locked.version != version:
        raise StaleVersionError()
    period: scheduling.GroupPeriod | None = None
    if locked.group_enrollment_id is not None:
        named = (
            await scheduling.named_group_periods(
                session, enrollment_ids=[locked.group_enrollment_id]
            )
        ).get(locked.group_enrollment_id)
        period = None if named is None else named.period
    zone = await administration.get_center_time_zone(session)
    return _Locked(
        plan=locked, period=period, zone=zone, today=local_date(clock.now(), zone)
    )


async def update_tuition_plan(
    session: AsyncSession, plan_id: uuid.UUID, body: TuitionPlanUpdate
) -> tuple[TuitionPlanDetail, bool]:
    """``PATCH /api/v1/billing/tuition-plans/{id}`` (D5). Returns the detail
    and whether ``ends_on`` moved later (the router defers the job)."""
    locked = await _lock_plan(session, plan_id, body.version)
    plan, today = locked.plan, locked.today
    changes: dict[str, Any] = {}
    if (
        body.due_offset_days is not None
        and body.due_offset_days != plan.due_offset_days
    ):
        changes["due_offset_days"] = body.due_offset_days
    extended = False
    if "ends_on" in body.model_fields_set and body.ends_on != plan.ends_on:
        new_end = body.ends_on
        through = (await billing.posted_through(session, [plan.id])).get(plan.id)
        if new_end is not None:
            first_end = billing.period_at(plan.starts_on, plan.frequency, 0).end
            if (
                not billing.is_period_end(plan.starts_on, plan.frequency, new_end)
                or new_end < first_end
                or (through is not None and new_end < through)
            ):
                raise billing.TuitionPlanEndsOnInvalidError()
        extended = new_end is None or (
            plan.ends_on is not None and new_end > plan.ends_on
        )
        if extended:
            if has_ended(plan, today, withdrawn_on(locked.period, locked.zone)):
                raise billing.TuitionPlanEndedError()
            if await billing.plan_overlaps(
                session,
                student_id=plan.student_id,
                service_price_id=plan.service_price_id,
                group_enrollment_id=plan.group_enrollment_id,
                starts_on=plan.starts_on,
                ends_on=new_end,
                exclude=plan.id,
            ):
                raise billing.TuitionPlanOverlapError()
            if locked.period is not None and plan.ends_on is not None:
                await billing.lock_billing_account(session, plan.billing_account_id)
                await _check_link(
                    session,
                    locked.period,
                    zone=locked.zone,
                    window_start=plan.ends_on + timedelta(days=1),
                    window_end=new_end,
                )
        changes["ends_on"] = new_end
    if changes:
        await billing.update_plan(session, plan.id, changes)
    return await get_tuition_plan(session, plan.id), extended


async def delete_tuition_plan(
    session: AsyncSession, plan_id: uuid.UUID, version: int
) -> None:
    """``DELETE /api/v1/billing/tuition-plans/{id}`` (D6): only a plan with
    no charge (``409 tuition_plan_has_charges``); its pauses go with it."""
    locked = await _lock_plan(session, plan_id, version)
    if await billing.plan_has_charges(session, locked.plan.id):
        raise billing.TuitionPlanHasChargesError()
    await billing.delete_plan(session, locked.plan.id)


async def add_tuition_pause(
    session: AsyncSession, plan_id: uuid.UUID, body: TuitionPauseCreate
) -> TuitionPlanDetail:
    """``POST /api/v1/billing/tuition-plans/{id}/pauses`` (D7, K4): ``409
    tuition_plan_ended`` → ``422 tuition_pause_invalid`` → ``409
    tuition_pause_overlap`` → insert; the plan's version is bumped."""
    locked = await _lock_plan(session, plan_id, body.version)
    plan, today = locked.plan, locked.today
    if has_ended(plan, today, withdrawn_on(locked.period, locked.zone)):
        raise billing.TuitionPlanEndedError()
    posted = (await billing.posted_period_starts(session, [plan.id])).get(
        plan.id, set()
    )
    current = today_period_start(plan, today) or plan.starts_on
    if (
        not billing.is_period_start(plan.starts_on, plan.frequency, body.starts_on)
        or body.starts_on < current
        or any(start >= body.starts_on for start in posted)
        or (plan.ends_on is not None and body.starts_on > plan.ends_on)
    ):
        raise billing.TuitionPauseInvalidError()
    if body.ends_on is not None and (
        body.ends_on < body.starts_on
        or not billing.is_period_end(plan.starts_on, plan.frequency, body.ends_on)
    ):
        raise billing.TuitionPauseInvalidError("ends_on")
    pauses = (await billing.plan_pauses(session, [plan.id])).get(plan.id, [])
    if any(
        p.starts_on <= (body.ends_on or date.max)
        and (p.ends_on is None or p.ends_on >= body.starts_on)
        for p in pauses
    ):
        raise billing.TuitionPauseOverlapError()
    await billing.add_pause(
        session, plan.id, starts_on=body.starts_on, ends_on=body.ends_on
    )
    await billing.update_plan(session, plan.id, {})
    return await get_tuition_plan(session, plan.id)


async def resume_tuition_plan(
    session: AsyncSession, plan_id: uuid.UUID, body: TuitionResume
) -> TuitionPlanDetail:
    """``POST /api/v1/billing/tuition-plans/{id}/resume`` (D8, K4): ``409
    tuition_pause_not_open`` → ``422 tuition_resume_invalid`` → the open
    pause ends the day before ``resume_on`` (or is deleted when
    ``resume_on`` is its own start); the plan's version is bumped."""
    locked = await _lock_plan(session, plan_id, body.version)
    plan, today = locked.plan, locked.today
    pauses = (await billing.plan_pauses(session, [plan.id])).get(plan.id, [])
    open_pause = next((p for p in pauses if p.ends_on is None), None)
    if open_pause is None:
        raise billing.TuitionPauseNotOpenError()
    current = today_period_start(plan, today) or plan.starts_on
    if (
        not billing.is_period_start(plan.starts_on, plan.frequency, body.resume_on)
        or body.resume_on < open_pause.starts_on
        or body.resume_on < current
    ):
        raise billing.TuitionResumeInvalidError()
    await billing.end_pause(
        session,
        open_pause.id,
        ends_on=None
        if body.resume_on == open_pause.starts_on
        else body.resume_on - timedelta(days=1),
    )
    await billing.update_plan(session, plan.id, {})
    return await get_tuition_plan(session, plan.id)
