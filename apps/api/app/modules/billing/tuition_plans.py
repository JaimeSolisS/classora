"""Tuition plan rows: reads and writes (#93 A3, A4, D, E, G).

The routes are ``app.workflows.tuition_plans_router``, the daily job
``app.workflows.tuition_charges``; both compose this module with families,
scheduling and administration (ADR-0002). Every function runs in the
caller's Unit of Work and never commits. The period grid and a period's
state are :mod:`app.modules.billing.tuition` (pure).

**Locks.** :func:`get_plan` takes the plan row ``FOR UPDATE`` (the plan
writes: ``PATCH``, ``DELETE``, pauses, resume) or ``FOR SHARE`` (the job's
plan step), after the caller's scheduling lock (a linked plan's group) and
before ``billing_settings`` and the account (data-model §6).

**Overlaps.** A violation of ``ex_tuition_plans_enrollment_overlap`` or
``ex_tuition_plans_student_item_overlap`` (flushed in a savepoint) is
``409 tuition_plan_overlap``; one of ``ex_tuition_plan_pauses_overlap`` is
``409 tuition_pause_overlap``. The callers check first, so the constraints
only decide a concurrent race.
"""

import uuid
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.money import Money, money_sum
from app.modules.billing.balances import (
    _effective_due_date,
    _valid_allocated,
    charge_status,
)
from app.modules.billing.errors import TuitionPauseOverlapError, TuitionPlanOverlapError
from app.modules.billing.models import (
    TUITION_PAUSE_OVERLAP,
    TUITION_PLAN_ENROLLMENT_OVERLAP,
    TUITION_PLAN_STUDENT_ITEM_OVERLAP,
    BillingAccount,
    Charge,
    ChargeAdjustment,
    PaymentAllocation,
    ServicePrice,
    TuitionPlan,
    TuitionPlanPause,
)
from app.modules.billing.schemas import ChargeStatus
from app.modules.billing.tuition import Frequency, PlanTerms

TUITION_PERIOD: Final = "tuition_period"
CANDIDATE_PAGE: Final = 200
"""Plans per keyset page of the daily job (#93 E3)."""

type PlanLock = Literal["share", "update"]


@dataclass(frozen=True)
class TuitionPlanRow:
    """A plan with its account's family."""

    id: uuid.UUID
    version: int
    billing_account_id: uuid.UUID
    family_id: uuid.UUID
    student_id: uuid.UUID
    service_price_id: uuid.UUID
    group_enrollment_id: uuid.UUID | None
    frequency: Frequency
    starts_on: date
    ends_on: date | None
    due_offset_days: int
    created_at: datetime

    @property
    def terms(self) -> PlanTerms:
        return PlanTerms(
            starts_on=self.starts_on, frequency=self.frequency, ends_on=self.ends_on
        )


@dataclass(frozen=True)
class PauseRow:
    """A pause of a plan."""

    id: uuid.UUID
    version: int
    starts_on: date
    ends_on: date | None

    @property
    def window(self) -> tuple[date, date | None]:
        return (self.starts_on, self.ends_on)


@dataclass(frozen=True)
class PostedPeriod:
    """A posted period: its charge, amount (net) and status (#90)."""

    period_start: date
    period_end: date
    charge_id: uuid.UUID
    amount: Money
    status: ChargeStatus


@dataclass(frozen=True)
class PerPeriodItem:
    """What a plan needs of its pinned catalog item."""

    id: uuid.UUID
    class_type: str
    pricing_model: str
    subject_id: uuid.UUID | None
    group_id: uuid.UUID | None
    period_frequency: str | None


def _plan_select() -> Any:
    return select(TuitionPlan, BillingAccount.family_id).join(
        BillingAccount, BillingAccount.id == TuitionPlan.billing_account_id
    )


def _row(found: Any) -> TuitionPlanRow:
    plan: TuitionPlan = found[0]
    return TuitionPlanRow(
        id=plan.id,
        version=plan.version,
        billing_account_id=plan.billing_account_id,
        family_id=found[1],
        student_id=plan.student_id,
        service_price_id=plan.service_price_id,
        group_enrollment_id=plan.group_enrollment_id,
        frequency=cast(Frequency, plan.frequency),
        starts_on=plan.starts_on,
        ends_on=plan.ends_on,
        due_offset_days=plan.due_offset_days,
        created_at=plan.created_at,
    )


# --- Reads ---------------------------------------------------------------------------


async def get_plan(
    session: AsyncSession, plan_id: uuid.UUID, *, lock: PlanLock | None = None
) -> TuitionPlanRow | None:
    """One plan (re-read), optionally locked ``FOR UPDATE`` or ``FOR
    SHARE`` (of the plan row only). One statement."""
    query = _plan_select().where(TuitionPlan.id == plan_id)
    if lock is not None:
        query = query.with_for_update(read=lock == "share", of=TuitionPlan)
    found = (
        await session.execute(query.execution_options(populate_existing=True))
    ).first()
    return None if found is None else _row(found)


async def list_plans(
    session: AsyncSession,
    *,
    student_id: uuid.UUID | None = None,
    family_id: uuid.UUID | None = None,
    group_enrollment_ids: Collection[uuid.UUID] | None = None,
) -> list[TuitionPlanRow]:
    """The plans matching every given filter (``group_enrollment_ids``: the
    plans linked to one of them), by ``starts_on`` descending and id. One
    statement (none for an empty ``group_enrollment_ids``)."""
    if group_enrollment_ids is not None and not group_enrollment_ids:
        return []
    query = _plan_select()
    if student_id is not None:
        query = query.where(TuitionPlan.student_id == student_id)
    if family_id is not None:
        query = query.where(BillingAccount.family_id == family_id)
    if group_enrollment_ids is not None:
        query = query.where(TuitionPlan.group_enrollment_id.in_(group_enrollment_ids))
    rows = await session.execute(
        query.order_by(TuitionPlan.starts_on.desc(), TuitionPlan.id)
    )
    return [_row(found) for found in rows]


async def tuition_candidates(
    session: AsyncSession,
    *,
    floor: date,
    today: date,
    after: uuid.UUID | None = None,
    limit: int = CANDIDATE_PAGE,
) -> list[TuitionPlanRow]:
    """One keyset page (by id) of the plans the daily job looks at (#93
    E3): ``starts_on <= today`` and ``coalesce(ends_on, today) >= floor``.
    One statement."""
    query = _plan_select().where(
        TuitionPlan.starts_on <= today,
        func.coalesce(TuitionPlan.ends_on, today) >= floor,
    )
    if after is not None:
        query = query.where(TuitionPlan.id > after)
    rows = await session.execute(query.order_by(TuitionPlan.id).limit(limit))
    return [_row(found) for found in rows]


async def plan_pauses(
    session: AsyncSession, plan_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[PauseRow]]:
    """The pauses of these plans by plan id, oldest first (plans without a
    pause are left out). One statement (none for no plan); re-read."""
    if not plan_ids:
        return {}
    rows = await session.scalars(
        select(TuitionPlanPause)
        .where(TuitionPlanPause.tuition_plan_id.in_(set(plan_ids)))
        .order_by(TuitionPlanPause.starts_on)
        .execution_options(populate_existing=True)
    )
    found: dict[uuid.UUID, list[PauseRow]] = {}
    for pause in rows:
        found.setdefault(pause.tuition_plan_id, []).append(
            PauseRow(
                id=pause.id,
                version=pause.version,
                starts_on=pause.starts_on,
                ends_on=pause.ends_on,
            )
        )
    return found


async def posted_period_starts(
    session: AsyncSession, plan_ids: Collection[uuid.UUID], *, since: date | None = None
) -> dict[uuid.UUID, set[date]]:
    """The ``service_date``s (period starts) of the ``tuition_period``
    charges of these plans (optionally from ``since`` on), by plan id. One
    statement (none for no plan)."""
    if not plan_ids:
        return {}
    query = select(Charge.tuition_plan_id, Charge.service_date).where(
        Charge.kind == TUITION_PERIOD, Charge.tuition_plan_id.in_(set(plan_ids))
    )
    if since is not None:
        query = query.where(Charge.service_date >= since)
    found: dict[uuid.UUID, set[date]] = {}
    for row in await session.execute(query):
        found.setdefault(row.tuition_plan_id, set()).add(row.service_date)
    return found


async def posted_through(
    session: AsyncSession, plan_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, date]:
    """The latest posted ``service_period_end`` of each plan with a charge.
    One statement (none for no plan)."""
    if not plan_ids:
        return {}
    rows = await session.execute(
        select(Charge.tuition_plan_id, func.max(Charge.service_period_end))
        .where(Charge.kind == TUITION_PERIOD, Charge.tuition_plan_id.in_(plan_ids))
        .group_by(Charge.tuition_plan_id)
    )
    return {row[0]: row[1] for row in rows if row[0] is not None and row[1] is not None}


async def plan_has_charges(session: AsyncSession, plan_id: uuid.UUID) -> bool:
    """Whether the plan has a charge. One statement."""
    found = await session.scalar(
        select(Charge.id).where(Charge.tuition_plan_id == plan_id).limit(1)
    )
    return found is not None


async def recent_periods(
    session: AsyncSession, plan_id: uuid.UUID, *, today: date, limit: int
) -> list[PostedPeriod]:
    """The plan's last ``limit`` posted periods, newest first, with the
    charge's net and status (#90 ``charge_status`` against ``today``). One
    statement."""
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    allocated = _valid_allocated(PaymentAllocation.charge_id == Charge.id)
    rows = await session.execute(
        select(
            Charge.id,
            Charge.service_date,
            Charge.service_period_end,
            Charge.amount,
            adjustments.label("adjustments"),
            allocated.label("allocated"),
            _effective_due_date().label("due_date"),
        )
        .where(Charge.kind == TUITION_PERIOD, Charge.tuition_plan_id == plan_id)
        .order_by(Charge.service_date.desc())
        .limit(limit)
    )
    found: list[PostedPeriod] = []
    for row in rows:
        net = row.amount + row.adjustments
        assert row.service_period_end is not None  # noqa: S101 - ck_charges_one_source
        found.append(
            PostedPeriod(
                period_start=row.service_date,
                period_end=row.service_period_end,
                charge_id=row.id,
                amount=net,
                status=charge_status(net, row.allocated, row.due_date, today),
            )
        )
    return found


async def per_period_item(
    session: AsyncSession, service_price_id: uuid.UUID
) -> PerPeriodItem | None:
    """The catalog item a plan pins (any pricing model; the caller checks
    it is ``per_period``), or ``None``. One statement."""
    row = (
        await session.execute(
            select(
                ServicePrice.id,
                ServicePrice.class_type,
                ServicePrice.pricing_model,
                ServicePrice.subject_id,
                ServicePrice.group_id,
                ServicePrice.period_frequency,
            ).where(ServicePrice.id == service_price_id)
        )
    ).first()
    if row is None:
        return None
    return PerPeriodItem(
        id=row.id,
        class_type=row.class_type,
        pricing_model=row.pricing_model,
        subject_id=row.subject_id,
        group_id=row.group_id,
        period_frequency=row.period_frequency,
    )


async def per_period_items(
    session: AsyncSession, ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, PerPeriodItem]:
    """:func:`per_period_item` for many ids. One statement (none for no
    id)."""
    if not ids:
        return {}
    rows = await session.execute(
        select(
            ServicePrice.id,
            ServicePrice.class_type,
            ServicePrice.pricing_model,
            ServicePrice.subject_id,
            ServicePrice.group_id,
            ServicePrice.period_frequency,
        ).where(ServicePrice.id.in_(set(ids)))
    )
    return {
        row.id: PerPeriodItem(
            id=row.id,
            class_type=row.class_type,
            pricing_model=row.pricing_model,
            subject_id=row.subject_id,
            group_id=row.group_id,
            period_frequency=row.period_frequency,
        )
        for row in rows
    }


def linked_plan_columns() -> Any:
    """``SELECT id, group_enrollment_id, starts_on, ends_on FROM
    tuition_plans WHERE group_enrollment_id IS NOT NULL``, **not executed**:
    the #92 enrollment classification checks whether a group period has a
    linked plan (K6, ``tuition_priced``)."""
    return select(
        TuitionPlan.id,
        TuitionPlan.group_enrollment_id,
        TuitionPlan.starts_on,
        TuitionPlan.ends_on,
    ).where(TuitionPlan.group_enrollment_id.is_not(None))


async def linked_plan_windows(
    session: AsyncSession, enrollment_ids: Collection[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[date, date | None]]]:
    """The windows ``(starts_on, ends_on)`` of the plans linked to these
    group enrollments, by enrollment id (K6: the #91 session classification).
    One statement (none for no id); re-read, so a plan committed meanwhile is
    seen under the caller's account lock."""
    if not enrollment_ids:
        return {}
    rows = await session.execute(
        select(
            TuitionPlan.group_enrollment_id, TuitionPlan.starts_on, TuitionPlan.ends_on
        )
        .where(TuitionPlan.group_enrollment_id.in_(set(enrollment_ids)))
        .execution_options(populate_existing=True)
    )
    found: dict[uuid.UUID, list[tuple[date, date | None]]] = {}
    for row in rows:
        found.setdefault(row.group_enrollment_id, []).append(
            (row.starts_on, row.ends_on)
        )
    return found


async def group_fee_net(
    session: AsyncSession, group_enrollment_id: uuid.UUID
) -> Money | None:
    """The net of the enrollment's ``group_enrollment`` charge, or ``None``
    without one (K6). One statement."""
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    row = (
        await session.execute(
            select(Charge.amount, adjustments.label("adjustments")).where(
                Charge.kind == "group_enrollment",
                Charge.group_enrollment_id == group_enrollment_id,
            )
        )
    ).first()
    return None if row is None else Money(row.amount + row.adjustments)


async def group_fee_enrollment_ids(
    session: AsyncSession, enrollment_ids: Collection[uuid.UUID]
) -> set[uuid.UUID]:
    """The group enrollments among these that have a ``group_enrollment``
    charge (#93 D9). One statement (none for no id)."""
    if not enrollment_ids:
        return set()
    rows = await session.scalars(
        select(Charge.group_enrollment_id).where(
            Charge.kind == "group_enrollment",
            Charge.group_enrollment_id.in_(set(enrollment_ids)),
        )
    )
    return {found for found in rows if found is not None}


async def positive_session_charges(
    session: AsyncSession,
    student_id: uuid.UUID,
    class_session_ids: Collection[uuid.UUID],
) -> bool:
    """Whether the student has a ``session`` charge with a net above
    ``0.00`` on one of these sessions (K6). One statement (none for no
    session)."""
    if not class_session_ids:
        return False
    adjustments = (
        select(money_sum(ChargeAdjustment.amount))
        .where(ChargeAdjustment.charge_id == Charge.id)
        .scalar_subquery()
    )
    found = await session.scalar(
        select(Charge.id)
        .where(
            Charge.kind == "session",
            Charge.student_id == student_id,
            Charge.class_session_id.in_(set(class_session_ids)),
            Charge.amount + adjustments > Money.zero(),
        )
        .limit(1)
    )
    return found is not None


# --- Writes --------------------------------------------------------------------------


def _overlap(window_start: date, window_end: date | None) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        or_(TuitionPlan.ends_on.is_(None), TuitionPlan.ends_on >= window_start)
    ]
    if window_end is not None:
        conditions.append(TuitionPlan.starts_on <= window_end)
    return conditions


async def plan_overlaps(
    session: AsyncSession,
    *,
    student_id: uuid.UUID,
    service_price_id: uuid.UUID,
    group_enrollment_id: uuid.UUID | None,
    starts_on: date,
    ends_on: date | None,
    exclude: uuid.UUID | None = None,
) -> bool:
    """Whether another plan of the same group enrollment (linked) or of the
    same student and item (standalone) overlaps ``[starts_on, ends_on]``
    (the A3 exclusions, checked first). One statement."""
    if group_enrollment_id is not None:
        same = [TuitionPlan.group_enrollment_id == group_enrollment_id]
    else:
        same = [
            TuitionPlan.group_enrollment_id.is_(None),
            TuitionPlan.student_id == student_id,
            TuitionPlan.service_price_id == service_price_id,
        ]
    query = select(TuitionPlan.id).where(*same, *_overlap(starts_on, ends_on))
    if exclude is not None:
        query = query.where(TuitionPlan.id != exclude)
    return await session.scalar(query.limit(1)) is not None


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        message = str(exc.orig)
        if (
            TUITION_PLAN_ENROLLMENT_OVERLAP in message
            or TUITION_PLAN_STUDENT_ITEM_OVERLAP in message
        ):
            raise TuitionPlanOverlapError() from None
        if TUITION_PAUSE_OVERLAP in message:
            raise TuitionPauseOverlapError() from None
        raise


async def create_plan(
    session: AsyncSession,
    *,
    billing_account_id: uuid.UUID,
    student_id: uuid.UUID,
    service_price_id: uuid.UUID,
    group_enrollment_id: uuid.UUID | None,
    frequency: Frequency,
    starts_on: date,
    ends_on: date | None,
    due_offset_days: int,
) -> uuid.UUID:
    """Insert a plan (``409 tuition_plan_overlap`` from the exclusions)."""
    plan = TuitionPlan(
        billing_account_id=billing_account_id,
        student_id=student_id,
        service_price_id=service_price_id,
        group_enrollment_id=group_enrollment_id,
        frequency=frequency,
        starts_on=starts_on,
        ends_on=ends_on,
        due_offset_days=due_offset_days,
    )
    session.add(plan)
    await _flush(session)
    return plan.id


async def _loaded_plan(session: AsyncSession, plan_id: uuid.UUID) -> TuitionPlan:
    plan = await session.get(TuitionPlan, plan_id)
    if plan is None:  # pragma: no cover - the caller locked it
        raise ValueError("unknown tuition plan")
    return plan


async def update_plan(
    session: AsyncSession, plan_id: uuid.UUID, changes: dict[str, Any]
) -> None:
    """Apply ``changes`` (``ends_on``, ``due_offset_days``) to a plan the
    caller locked; an empty ``changes`` still bumps its version (a pause or
    a resume). ``409 tuition_plan_overlap`` from the exclusions."""
    plan = await _loaded_plan(session, plan_id)
    if changes:
        for key, value in changes.items():
            setattr(plan, key, value)
    else:
        plan.updated_at = func.now()
    await _flush(session)


async def delete_plan(session: AsyncSession, plan_id: uuid.UUID) -> None:
    """Delete a plan the caller locked and its pauses (each audited; the
    caller checked it has no charge)."""
    pauses = await session.scalars(
        select(TuitionPlanPause).where(TuitionPlanPause.tuition_plan_id == plan_id)
    )
    for pause in pauses:
        await session.delete(pause)
    await session.delete(await _loaded_plan(session, plan_id))
    await session.flush()


async def add_pause(
    session: AsyncSession, plan_id: uuid.UUID, *, starts_on: date, ends_on: date | None
) -> uuid.UUID:
    """Insert a pause (``409 tuition_pause_overlap`` from the exclusion)."""
    pause = TuitionPlanPause(
        tuition_plan_id=plan_id, starts_on=starts_on, ends_on=ends_on
    )
    session.add(pause)
    await _flush(session)
    return pause.id


async def end_pause(
    session: AsyncSession, pause_id: uuid.UUID, *, ends_on: date | None
) -> None:
    """Set a pause's end, or delete it when ``ends_on`` is ``None`` (a pause
    resumed on its own start, K4)."""
    pause = await session.get(TuitionPlanPause, pause_id)
    if pause is None:  # pragma: no cover - read under the plan lock
        raise ValueError("unknown pause")
    if ends_on is None:
        await session.delete(pause)
    else:
        pause.ends_on = ends_on
    await _flush(session)


def plan_ids_of(plans: Sequence[TuitionPlanRow]) -> list[uuid.UUID]:
    """The ids of ``plans``, in order."""
    return [plan.id for plan in plans]
