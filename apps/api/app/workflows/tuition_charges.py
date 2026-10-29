"""Tuition charges (#93 M6-08 E, G; spec §6, W7.2; ADR-0006 E1, E7, E9,
E11; ``_docs/data-model.md`` §3.17 and "Tuition plans (#93)").

The daily job ``billing:post_tuition_charges``
(:mod:`app.workflows.tuition_charge_tasks`, ``30 7 * * *``, also deferred
after a plan create, a resume, an ``ends_on`` extension and a billing
settings ``PATCH`` that sets a date) posts **exactly one** ``tuition_period``
charge per plan per period, on the natural key ``(tuition_plan_id,
service_date = period_start)`` (``uq_charges_tuition_period``, #90 E4). It
never uses ``idempotency_keys`` (E1).

**The rules, in one place** (:func:`period_outcomes`, used by the job, the
unbilled read and the plan detail, so a listed period is exactly a period a
run skips with that reason), for each unposted period of a plan, oldest
first:

1. the period's state (``billing.service.plan_period_state``, the pure K3-K6
   rule): ``not_due`` (``period_start`` after the center's local today) and
   ``after_end`` (after the plan's ``ends_on``) are not looked at;
   ``below_floor`` (before ``billing_starts_on``), ``paused`` (K4: never
   caught up) and ``enrollment_ended`` (the linked group enrollment was
   withdrawn on or before the period's start, K6) are skipped;
2. ``family_changed`` (K9): the student's current family is not the family
   whose account the plan bills (no family, or another one) → skipped and
   listed, retried daily (staff end the plan and create one for the new
   family);
3. ``no_price``: the pinned item's base price on ``period_start``
   (``billing.service.quote`` with ``PriceQuery.service_price_id``: the
   item's custom prices, then its version in effect; K2) is ``None`` →
   skipped, listed, retried daily (a later version posts every missing
   period, oldest first);
4. ``due``: posted at the quote (the discounts after the base price,
   snapshotted as ``charge_discounts``; the student's current level
   snapshotted on the charge). A ``0.00`` version posts a ``0.00`` charge
   (status ``void``).

**Charge fields:** ``kind='tuition_period'``, ``tuition_plan_id``,
``service_date = period_start``, ``service_period_end = period_end``;
``due_date = period_start + plan.due_offset_days`` plus the account's
``due_offset_days`` (E11), set at posting and never updated. A posted charge
is never changed by this job: a new price version, a pause, an end or a
withdrawal changes only later periods (crediting a posted period is #101).

**Candidates** (E3): the plans with ``starts_on <= today`` and
``coalesce(ends_on, today) >= billing_starts_on``, keyset pages of
:data:`~app.modules.billing.tuition_plans.CANDIDATE_PAGE` by id until a
short page; per page one statement reads the posted period starts (from the
floor on) and one the pauses. A plan with no period to try (all posted,
paused or not due) is counted from the page and opens no Unit of Work.

**Per plan, its own Unit of Work** (``actor_id=None``), in the data-model
§6 lock order: (linked) the group row ``FOR SHARE``
(``scheduling.service.lock_group_for_billing``) → the plan row ``FOR SHARE``
(the plan writes take it ``FOR UPDATE``, so a pause waits for a posting or
the posting waits for the pause) → ``billing_settings FOR SHARE``
(``billing_starts_on(lock=True)``; ``NULL`` → nothing) → the enrollment,
pauses, posted periods, family and level re-read → for each period, the
rules above → ``billing.service.post_charge`` (the account row ``FOR
UPDATE``, last). ``created = False`` counts as ``already_posted``. A
failure rolls back that plan only, is logged with the ids and the error
class, and the run continues.

**Counts** (:class:`TuitionChargeRunResult`, one info line with counts
only): ``plans`` (candidates), ``periods_due`` (unposted periods from the
floor to today, before the plan's end), ``posted``, ``already_posted``,
``paused``, ``below_floor``, ``enrollment_ended``, ``no_price``,
``family_changed``, ``failed`` (plans). Billing off: nothing is read, every
count is 0. The run reads the center zone once and takes ``today`` there.

**Run by hand** (:func:`run_post_tuition_charges` builds its own direct
engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.tuition_charges import run_post_tuition_charges as run; \\
    print(asyncio.run(run(get_settings())))"

**Unbilled tuition periods** (:func:`list_unbilled_tuition_periods`, ``GET
/api/v1/billing/unbilled-tuition-periods``, G1): every due, unposted period
the rules classify ``no_price`` or ``family_changed``; billing off → an
empty page. Computed on read: no lock, no write; at most twelve statements
(the floor, the zone, the plans, their pauses, their posted periods, the
linked enrollments, the students' families, the items, the custom prices
and the versions of the pinned items, the student names, the subject
names).
"""

import logging
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.pagination import OffsetPage, OffsetParams
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.catalog import service as catalog
from app.modules.families import service as families
from app.modules.families.schemas import FamilyReference
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import local_date
from app.workflows.tuition_plan_schemas import (
    TuitionRecord,
    TuitionUnbilledReason,
    UnbilledTuitionItem,
)

logger = logging.getLogger(__name__)

UNBILLED_REASONS: Final[tuple[TuitionUnbilledReason, ...]] = (
    "no_price",
    "family_changed",
)

type Outcome = Literal[
    "below_floor", "paused", "enrollment_ended", "family_changed", "priceable"
]
"""A period's outcome before the price step (``priceable``: rules 1-2 pass)."""


# --- The rules (E4, G1) --------------------------------------------------------------


@dataclass(frozen=True)
class PlanFacts:
    """What :func:`period_outcomes` needs of one plan, read in the caller's
    Unit of Work."""

    plan: billing.TuitionPlanRow
    pauses: Sequence[tuple[date, date | None]]
    posted: Collection[date]
    withdrawn_on: date | None
    """The local date the linked enrollment was withdrawn (``None``: not
    withdrawn, or a standalone plan)."""
    current_family_id: uuid.UUID | None


def period_outcomes(
    facts: PlanFacts, *, first: date, today: date, floor: date | None
) -> list[tuple[billing.Period, Outcome]]:
    """Rules 1-2 of the module docstring for each **unposted** period of the
    plan whose start lies in ``[first, today]`` (and before its end), oldest
    first."""
    plan = facts.plan
    found: list[tuple[billing.Period, Outcome]] = []
    for period in billing.periods_between(
        plan.starts_on, plan.frequency, max(first, plan.starts_on), today
    ):
        if period.start in facts.posted:
            continue
        state = billing.plan_period_state(
            plan.terms,
            facts.pauses,
            period,
            today=today,
            floor=floor,
            enrollment=facts.withdrawn_on,
        )
        if state in ("not_due", "after_end"):
            continue
        if state != "due":
            found.append((period, cast(Outcome, state)))
        elif facts.current_family_id != plan.family_id:
            found.append((period, "family_changed"))
        else:
            found.append((period, "priceable"))
    return found


def price_query(
    plan: billing.TuitionPlanRow,
    item: billing.PerPeriodItem,
    period: billing.Period,
    *,
    education_level_id: uuid.UUID | None,
) -> billing.PriceQuery:
    """The pinned :class:`~app.modules.billing.schemas.PriceQuery` of one
    period (K2, E4): the item's class type, subject and group scope, ``on``
    = the period's start, the student's current level, the plan's family."""
    assert item.subject_id is not None  # noqa: S101 - per_period items have one
    return billing.PriceQuery(
        student_id=plan.student_id,
        family_id=plan.family_id,
        charge_kind="tuition_period",
        class_type=cast(Any, item.class_type),
        pricing_model="per_period",
        subject_id=item.subject_id,
        on=period.start,
        education_level_id=education_level_id,
        duration_minutes=None,
        group_id=item.group_id,
        service_price_id=item.id,
    )


def withdrawn_on(period: scheduling.GroupPeriod | None, zone: str) -> date | None:
    """The local date a linked enrollment was withdrawn, or ``None``."""
    if period is None or period.withdrawn_at is None:
        return None
    return local_date(period.withdrawn_at, zone)


# --- The job (E1-E5) -----------------------------------------------------------------


@dataclass(frozen=True)
class TuitionChargeRunResult:
    """The counts of one run (also its log line)."""

    plans: int = 0
    periods_due: int = 0
    posted: int = 0
    already_posted: int = 0
    paused: int = 0
    below_floor: int = 0
    enrollment_ended: int = 0
    no_price: int = 0
    family_changed: int = 0
    failed: int = 0


async def post_plan(
    session: AsyncSession,
    plan_id: uuid.UUID,
    *,
    zone: str,
    today: date,
    first: date,
) -> Counter[str]:
    """E4 for one plan in the caller's Unit of Work (the lock order of the
    module docstring), for its unposted periods from ``first`` to
    ``today``. Returns the outcome of each period it looked at."""
    counts: Counter[str] = Counter()
    unlocked = await billing.get_plan(session, plan_id)
    if unlocked is None:
        return counts
    if unlocked.group_enrollment_id is not None:
        before = await scheduling.named_group_periods(
            session, enrollment_ids=[unlocked.group_enrollment_id]
        )
        found = before.get(unlocked.group_enrollment_id)
        if found is not None:
            await scheduling.lock_group_for_billing(session, found.period.group_id)
    plan = await billing.get_plan(session, plan_id, lock="share")
    if plan is None:
        return counts
    floor = await billing.billing_starts_on(session, lock=True)
    if floor is None:
        return counts
    enrollment: scheduling.GroupPeriod | None = None
    if plan.group_enrollment_id is not None:
        # Re-read after the group lock (a withdrawal committed meanwhile).
        after = await scheduling.named_group_periods(
            session, enrollment_ids=[plan.group_enrollment_id]
        )
        named = after.get(plan.group_enrollment_id)
        enrollment = None if named is None else named.period
    pauses = (await billing.plan_pauses(session, [plan.id])).get(plan.id, [])
    posted = (await billing.posted_period_starts(session, [plan.id])).get(
        plan.id, set()
    )
    family = await families.family_of_student(session, plan.student_id)
    facts = PlanFacts(
        plan=plan,
        pauses=[p.window for p in pauses],
        posted=posted,
        withdrawn_on=withdrawn_on(enrollment, zone),
        current_family_id=None if family is None else family.id,
    )
    outcomes = period_outcomes(facts, first=first, today=today, floor=floor)
    if not any(outcome == "priceable" for _, outcome in outcomes):
        counts.update(outcome for _, outcome in outcomes)
        return counts
    item = await billing.per_period_item(session, plan.service_price_id)
    student = (await families.student_level_references(session, [plan.student_id])).get(
        plan.student_id
    )
    level = None if student is None else student.education_level_id
    for period, outcome in outcomes:
        if outcome != "priceable":
            counts[outcome] += 1
            continue
        assert item is not None  # noqa: S101 - RESTRICT keeps the pinned item
        quote = await billing.quote(
            session, price_query(plan, item, period, education_level_id=level)
        )
        if quote is None:
            counts["no_price"] += 1
            continue
        try:
            charged = await billing.post_charge(
                session,
                billing.ChargeDraft.priced(
                    quote,
                    student_id=plan.student_id,
                    kind="tuition_period",
                    tuition_plan_id=plan.id,
                    service_date=period.start,
                    service_period_end=period.end,
                    education_level_id=level,
                    due_date_base=period.start + timedelta(days=plan.due_offset_days),
                ),
            )
        except billing.StudentHasNoFamilyError:
            counts["family_changed"] += 1
            continue
        counts["posted" if charged.created else "already_posted"] += 1
    return counts


def _page_outcomes(
    plan: billing.TuitionPlanRow,
    pauses: Sequence[tuple[date, date | None]],
    posted: Collection[date],
    *,
    first: date,
    today: date,
    floor: date,
) -> list[str]:
    """The page-level view of a plan's unposted periods: their state with
    the page's pauses and posted periods (no enrollment, no family)."""
    states: list[str] = []
    for period in billing.periods_between(
        plan.starts_on, plan.frequency, max(first, plan.starts_on), today
    ):
        if period.start in posted:
            continue
        state = billing.plan_period_state(
            plan.terms, pauses, period, today=today, floor=floor
        )
        if state not in ("not_due", "after_end"):
            states.append(state)
    return states


async def run_post_tuition_charges(
    settings: Settings, *, now: datetime | None = None
) -> TuitionChargeRunResult:
    """One run: every candidate plan, one Unit of Work each (only for a plan
    with a period to try). ``now`` (default :func:`app.core.clock.now`)
    gives the center's local ``today``. Returns the counts."""
    engine = create_worker_engine(settings)
    counts: Counter[str] = Counter()
    plans = periods_due = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            floor = await billing.billing_starts_on(uow.session)
            zone = (
                ""
                if floor is None
                else await administration.get_center_time_zone(uow.session)
            )
        today = local_date(now or clock.now(), zone) if floor is not None else None
        after: uuid.UUID | None = None
        pending = floor is not None
        while pending and floor is not None and today is not None:
            async with UnitOfWork(sessionmaker) as uow:
                page = await billing.tuition_candidates(
                    uow.session, floor=floor, today=today, after=after
                )
                ids = billing.plan_ids_of(page)
                pauses = await billing.plan_pauses(uow.session, ids)
                posted = await billing.posted_period_starts(
                    uow.session, ids, since=floor
                )
            pending = len(page) == billing.CANDIDATE_PAGE
            after = page[-1].id if page else None
            for plan in page:
                plans += 1
                windows = [p.window for p in pauses.get(plan.id, [])]
                states = _page_outcomes(
                    plan,
                    windows,
                    posted.get(plan.id, set()),
                    first=floor,
                    today=today,
                    floor=floor,
                )
                periods_due += len(states)
                if "due" not in states:
                    counts.update(states)
                    continue
                try:
                    async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                        done = await post_plan(
                            uow.session, plan.id, zone=zone, today=today, first=floor
                        )
                except Exception as exc:  # noqa: BLE001 - one plan never blocks the rest
                    failed += 1
                    logger.error(
                        "Posting tuition charges failed",
                        extra={
                            "tuition_plan_id": str(plan.id),
                            "student_id": str(plan.student_id),
                            "error": type(exc).__name__,
                        },
                    )
                    continue
                counts.update(done)
    finally:
        await engine.dispose()
    result = TuitionChargeRunResult(
        plans=plans,
        periods_due=periods_due,
        posted=counts["posted"],
        already_posted=counts["already_posted"],
        paused=counts["paused"],
        below_floor=counts["below_floor"],
        enrollment_ended=counts["enrollment_ended"],
        no_price=counts["no_price"],
        family_changed=counts["family_changed"],
        failed=failed,
    )
    logger.info(
        "Posted tuition charges",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


# --- Unbilled tuition periods (G1) ---------------------------------------------------


@dataclass(frozen=True)
class UnbilledPeriod:
    """A due, unposted period classified ``no_price`` or
    ``family_changed``."""

    plan: billing.TuitionPlanRow
    period: billing.Period
    reason: TuitionUnbilledReason


@dataclass(frozen=True)
class PlanContext:
    """The :class:`PlanFacts` of many plans by plan id, their linked
    enrollments by enrollment id and the students' current families."""

    facts: dict[uuid.UUID, PlanFacts]
    enrollments: dict[uuid.UUID, scheduling.NamedGroupPeriod]
    families: Mapping[uuid.UUID, FamilyReference]


async def plan_context(
    session: AsyncSession,
    plans: Sequence[billing.TuitionPlanRow],
    *,
    zone: str,
    since: date | None = None,
) -> PlanContext:
    """The :class:`PlanContext` of many plans: their pauses, posted periods
    (from ``since`` on), linked enrollments and the students' families
    (four statements, none for no plan)."""
    if not plans:
        return PlanContext(facts={}, enrollments={}, families={})
    ids = billing.plan_ids_of(plans)
    pauses = await billing.plan_pauses(session, ids)
    posted = await billing.posted_period_starts(session, ids, since=since)
    enrollments = await scheduling.named_group_periods(
        session,
        enrollment_ids={
            p.group_enrollment_id for p in plans if p.group_enrollment_id is not None
        },
    )
    family_of = await families.families_of_students(
        session, {p.student_id for p in plans}
    )
    facts: dict[uuid.UUID, PlanFacts] = {}
    for plan in plans:
        named = (
            None
            if plan.group_enrollment_id is None
            else enrollments.get(plan.group_enrollment_id)
        )
        family = family_of.get(plan.student_id)
        facts[plan.id] = PlanFacts(
            plan=plan,
            pauses=[p.window for p in pauses.get(plan.id, [])],
            posted=posted.get(plan.id, set()),
            withdrawn_on=withdrawn_on(None if named is None else named.period, zone),
            current_family_id=None if family is None else family.id,
        )
    return PlanContext(facts=facts, enrollments=enrollments, families=family_of)


async def unbilled_periods(
    session: AsyncSession,
    facts: Mapping[uuid.UUID, PlanFacts],
    items: Mapping[uuid.UUID, billing.PerPeriodItem],
    *,
    first: date,
    today: date,
    floor: date,
) -> tuple[list[UnbilledPeriod], dict[tuple[uuid.UUID, date], TuitionUnbilledReason]]:
    """The rules for every plan in ``facts``: the unbilled periods (oldest
    first per plan) and the reason of every unbilled ``(plan, start)``. The
    price step reads the custom prices and versions of every pinned item in
    two statements (none when nothing is priceable)."""
    pending: list[tuple[billing.TuitionPlanRow, billing.Period]] = []
    found: list[UnbilledPeriod] = []
    for plan_facts_ in facts.values():
        for period, outcome in period_outcomes(
            plan_facts_, first=first, today=today, floor=floor
        ):
            if outcome == "family_changed":
                found.append(UnbilledPeriod(plan_facts_.plan, period, "family_changed"))
            elif outcome == "priceable":
                pending.append((plan_facts_.plan, period))
    queries = {
        (plan.id, period.start): price_query(
            plan, items[plan.service_price_id], period, education_level_id=None
        )
        for plan, period in pending
        if plan.service_price_id in items
    }
    prices = await billing.pinned_base_prices(session, list(queries.values()))
    for plan, period in pending:
        query = queries.get((plan.id, period.start))
        if query is None or prices.get(query) is None:
            found.append(UnbilledPeriod(plan, period, "no_price"))
    reasons = {(u.plan.id, u.period.start): u.reason for u in found}
    return found, reasons


async def _names(
    session: AsyncSession, ids: Collection[uuid.UUID], columns: object
) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    query = columns.where(columns.selected_columns[0].in_(set(ids)))  # type: ignore[attr-defined]
    return {row[0]: row[1] for row in await session.execute(query)}


async def list_unbilled_tuition_periods(
    session: AsyncSession,
    *,
    reasons: Collection[TuitionUnbilledReason] = (),
    student_id: uuid.UUID | None = None,
    page: OffsetParams,
) -> OffsetPage[UnbilledTuitionItem]:
    """The due, unposted periods the job skips as ``no_price`` or
    ``family_changed`` (``reasons``: only those; empty = both), optionally
    one student's, by period start, student name and plan id. Billing off:
    an empty page. No lock, no write; bounded statements (module
    docstring)."""
    empty = OffsetPage[UnbilledTuitionItem](
        items=[], total=0, limit=page.limit, offset=page.offset
    )
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return empty
    zone = await administration.get_center_time_zone(session)
    today = local_date(clock.now(), zone)
    plans = [
        plan
        for plan in await billing.list_plans(session, student_id=student_id)
        if plan.starts_on <= today and (plan.ends_on is None or plan.ends_on >= floor)
    ]
    context = await plan_context(session, plans, zone=zone, since=floor)
    items = await billing.per_period_items(session, {p.service_price_id for p in plans})
    found, _ = await unbilled_periods(
        session, context.facts, items, first=floor, today=today, floor=floor
    )
    wanted = set(reasons) or set(UNBILLED_REASONS)
    found = [u for u in found if u.reason in wanted]
    students = await families.student_references(
        session, {u.plan.student_id for u in found}
    )

    def _name(student: uuid.UUID) -> str:
        reference = students.get(student)
        return "" if reference is None else reference.display_name

    found.sort(
        key=lambda u: (u.period.start, _name(u.plan.student_id).casefold(), u.plan.id)
    )
    window = found[page.offset : page.offset + page.limit]
    subjects = await _names(
        session,
        {
            item.subject_id
            for u in window
            if (item := items.get(u.plan.service_price_id)) is not None
            and item.subject_id is not None
        },
        catalog.subject_name_columns(),
    )
    result: list[UnbilledTuitionItem] = []
    for unbilled in window:
        plan = unbilled.plan
        item = items[plan.service_price_id]
        assert item.subject_id is not None  # noqa: S101 - per_period items have one
        family = context.families.get(plan.student_id)
        named = (
            None
            if plan.group_enrollment_id is None
            else context.enrollments.get(plan.group_enrollment_id)
        )
        result.append(
            UnbilledTuitionItem(
                tuition_plan_id=plan.id,
                period_start=unbilled.period.start,
                period_end=unbilled.period.end,
                frequency=plan.frequency,
                student=TuitionRecord(id=plan.student_id, name=_name(plan.student_id)),
                family=None
                if family is None
                else TuitionRecord(id=family.id, name=family.name),
                subject=TuitionRecord(
                    id=item.subject_id, name=subjects.get(item.subject_id, "")
                ),
                group=None
                if named is None
                else TuitionRecord(id=named.period.group_id, name=named.group_name),
                reason=unbilled.reason,
            )
        )
    return OffsetPage[UnbilledTuitionItem](
        items=result, total=len(found), limit=page.limit, offset=page.offset
    )
