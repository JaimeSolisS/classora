"""Group and course enrollment charges (#92 M6-07; spec §3, §6, W4.1, W4.2;
ADR-0006 E1, E9, E11; ``_docs/data-model.md`` §3.9, §3.10, §6 and
"Enrollment charges (#92)").

Every **cohort enrollment period** gets the cohort's course fee, and every
**fee-priced group enrollment period** the group's flat fee, exactly once
(``kind`` ``cohort_enrollment`` / ``group_enrollment``, natural keys on the
enrollment ids, #90 E4). They are posted **in the M4 hooks' Unit of Work**
(:func:`app.workflows.cohort_enrollment.apply_cohort_enrollment_change`,
:func:`app.workflows.group_enrollment.apply_enrollment_change`, E1) and by
the periodic job ``billing:post_enrollment_charges``
(:mod:`app.workflows.enrollment_charge_tasks`, every 15 minutes), which
finds the periods the hooks skipped (billing off, no price, no family).
Session-priced groups are charged per session by
``billing:post_session_charges`` (:mod:`app.workflows.session_charges`).

**The rules, in one place** (:func:`classify_periods`, used by the hooks,
the sweeper and the unbilled read, so a listed period is exactly a period
the sweeper skips with that reason), for a period in this order:

1. ``posted_already``: the period has its fee charge;
2. ``below_floor`` (J6): the due-date base ``max(enrollment local date,
   local date of the owner's first counted session)`` is before
   ``billing_starts_on`` (history from before go-live is never charged; an
   advance enrollment for a course that starts after go-live is);
3. **group:** ``session_priced`` (J2): a ``session`` charge of the student
   exists on a group session inside the period (``[enrolled_at,
   withdrawn_at)``), so the period is priced per session;
4. no fee price: ``billing.service.base_price`` of the period's fee
   (``course/full_course`` with the cohort, or ``group/full_course`` with
   the group; the owner's subject, ``on`` = the enrollment's local date,
   the student's current level, no duration) is ``None`` → group:
   ``session_priced``; cohort: ``no_price``;
5. ``already_charged`` (J5): another period of the student in the same
   group or cohort has a fee charge whose net is above ``0.00`` (a
   withdraw-and-re-enroll never charges the course twice; once #101 credits
   the fee to ``0.00``, a new period is charged again);
6. ``no_billing_account``: the student has no family;
7. ``billable``: the base price; when posting, the proration and the quote.

**Precedence (choice):** for a cohort period ``no_price`` comes **before**
``no_billing_account`` (the reverse of #91 G4), because the fee price
doesn't depend on the family.

**Proration (J3)** of a ``billable`` period, at posting: the owner's counted
occurrences (``scheduling.service.enrollment_session_counts``: materialized
and projected, not cancelled or skipped). A cohort's denominator is its
snapshot's ``session_count`` (``courses.service.cohort_session_count``) and
``remaining`` (the counted occurrences that start after ``enrolled_at``) is
clamped to it; a group is prorated only when every series of it is finite
(``total`` = its counted occurrences), an open-ended group pays the full fee
and its charge has no proration. ``quote(…, prorate=(remaining, total))``
prorates the base price **before** the discounts; the counts are
snapshotted on the charge (J8). ``remaining = 0`` gives a ``0.00`` charge
(status ``void``). A finite group with no counted occurrence at all
(``total = 0``) gets a ``0.00`` charge with no proration (D-choice).

**Service date** = the enrollment's local date; **due date** = the J6 base
plus the account's ``due_offset_days`` (E11).

**Posting** (:func:`post_enrollment_fee`, B4): the student's family → its
account (``ensure_billing_account``) → the account row ``FOR UPDATE`` →
steps 1, 3 and 5 re-read **under the lock** (J2, J5: a session charge and
the fee of one period are both posted under the family's account lock, so
whichever commits first wins and the other posts nothing) →
``billing.service.post_charge``. ``created = False`` is a success; a family
removed meanwhile is ``no_billing_account``. ``posted_by_user_id`` is the
Unit of Work's actor: the enrolling user in a hook, ``NULL`` in the job.

**The hooks** (C):

- **cohort, ``enrolled``:** ``billing_starts_on(lock=True)`` (``FOR
  SHARE``, after the M4 locks) → classify → post when ``billable``;
- **cohort, ``withdrawn``:** nothing (a course fee changes only through
  #101);
- **group, ``enrolled``:** first the J4 **restorations**: each ``session``
  charge of the student on a group session after ``effective_at`` where
  the student is a participant again, whose ``enrollment_removal``
  adjustments add up below ``0.00``, gets a positive ``enrollment_removal``
  of minus that sum (``source_key enrollment_removal:<charge_id>:
  <enrollment_id>:enrolled``); then the floor, the classification and the
  fee as for a cohort;
- **group, ``withdrawn``:** the J4 **credits**: each ``session`` charge of
  the student on a group session that starts after ``effective_at`` and
  that the student just left, with a net above ``0.00``, gets an
  ``enrollment_removal`` of ``−net`` (``…:<enrollment_id>:withdrawn``). A
  flat group fee is never touched (#101).

Credits and restorations ignore the floor (they correct posted charges) and
never edit a charge. Every expected outcome writes nothing and raises
nothing (J10); any other exception propagates and rolls the enrollment back
(the M4 contract).

**The sweeper** (:func:`run_post_enrollment_charges`): candidates are the
cohort periods with no ``cohort_enrollment`` charge, and the group periods
with no ``group_enrollment`` charge whose group has a ``group/full_course``
item (scoped to it, or unscoped for its subject), both with
``GREATEST(enrolled_at, first counted session start)`` at or after the
floor's local midnight; keyset pages of :data:`PAGE_SIZE` by
``(enrolled_at, enrollment_id)`` until a short page. **Per candidate, its
own Unit of Work** (no actor): the group row (``scheduling.service.
lock_group_for_billing``) or the cohort row (``courses.service.
lock_cohort_for_billing``) ``FOR SHARE`` → ``billing_starts_on(lock=True)``
→ classify → post. A failing candidate is logged (ids and the error class
only) and the run continues.

**Run by hand** (:func:`run_post_enrollment_charges` builds its own direct
engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.enrollment_charges import run_post_enrollment_charges as run; \\
    print(asyncio.run(run(get_settings())))"

**Unbilled enrollments** (:func:`list_unbilled_enrollments`, ``GET
/api/v1/billing/unbilled-enrollments``): the sweeper's candidates
classified ``no_billing_account`` or ``no_price``, computed on read (no
lock, no write); billing off → an empty page. At most thirteen
statements (the floor, the zone, the candidates and the ledger facts of
each kind, the owners' schedules (three), the students, the families, the
custom prices, the level names) plus the memoized catalog lookups.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import Select, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.pagination import OffsetPage, OffsetParams
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.catalog import service as catalog
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.families.schemas import FamilyReference
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import (
    EnrollmentKind,
    Period,
    PeriodFacts,
    PriceMemo,
    fee_price_query,
    floor_instant,
    local_date,
    period_columns,
    period_facts,
)
from app.workflows.enrollment_charge_schemas import UnbilledEnrollmentItem
from app.workflows.session_charge_schemas import UnbilledReason, UnbilledRecord

logger = logging.getLogger(__name__)

PAGE_SIZE: Final = 200
"""Candidates per keyset page (D3)."""
KINDS: Final[tuple[EnrollmentKind, ...]] = ("cohort_enrollment", "group_enrollment")
UNBILLED_REASONS: Final[tuple[UnbilledReason, ...]] = ("no_billing_account", "no_price")
REMOVAL_KEY: Final = "enrollment_removal:{charge_id}:{enrollment_id}:{change}"
"""The J4 ``source_key`` of a removal credit (``withdrawn``) or a
restoration (``enrolled``)."""

type Outcome = Literal[
    "posted_already",
    "below_floor",
    "already_charged",
    "session_priced",
    "no_billing_account",
    "no_price",
    "billable",
]
type FeeOutcome = Literal[
    "posted",
    "already_posted",
    "below_floor",
    "already_charged",
    "session_priced",
    "no_billing_account",
    "no_price",
    "not_billable",
]


@dataclass(frozen=True)
class PeriodOutcome:
    """A period's classification (B1). ``service_date`` is the enrollment's
    local date and ``due_date_base`` the J6 base; ``price`` (the base price)
    and ``family`` are set for ``billable``; ``quote`` and ``proration``
    are filled by :func:`classify_enrollment_period` for ``billable``."""

    outcome: Outcome
    service_date: date
    due_date_base: date
    education_level_id: uuid.UUID | None = None
    family: FamilyReference | None = None
    price: billing.BasePrice | None = None
    counts: scheduling.SessionCounts | None = None
    quote: billing.Quote | None = None
    proration: tuple[int, int] | None = None


# --- The classification (B1) ---------------------------------------------------------


def _due_date_base(
    period: Period, counts: scheduling.SessionCounts, zone: str
) -> tuple[date, date]:
    service_date = local_date(period.enrolled_at, zone)
    first = counts.first_start
    if first is None:
        return service_date, service_date
    return service_date, max(service_date, local_date(first, zone))


async def classify_periods(
    session: AsyncSession,
    periods: Sequence[Period],
    *,
    zone: str,
    floor: date,
    prices: PriceMemo | None = None,
    prefetch: bool = False,
    family_by_student: dict[uuid.UUID, FamilyReference] | None = None,
    students: dict[uuid.UUID, families.StudentLevelReference] | None = None,
) -> list[PeriodOutcome]:
    """The rules of the module docstring for each period (in order). A
    bounded number of statements whatever the number of periods: the ledger
    facts (one per kind), the owners' schedules (at most three), the
    families and the students' levels (unless given), optionally the custom
    prices in one batch (``prefetch``), plus the memoized catalog
    lookups."""
    if not periods:
        return []
    prices = PriceMemo(session) if prices is None else prices
    facts = await period_facts(session, periods)
    schedules = await scheduling.owner_schedules(session, {p.owner for p in periods})
    student_ids = {p.student_id for p in periods}
    if family_by_student is None:
        family_by_student = await families.families_of_students(session, student_ids)
    if students is None:
        students = await families.student_level_references(session, student_ids)
    levels = {sid: ref.education_level_id for sid, ref in students.items()}
    prepared: list[tuple[Period, scheduling.SessionCounts, date, date]] = []
    for period in periods:
        counts = schedules[period.owner].counts(period.enrolled_at)
        service_date, base = _due_date_base(period, counts, zone)
        prepared.append((period, counts, service_date, base))
    if prefetch:
        await prices.prefetch(
            [
                fee_price_query(
                    period,
                    service_date=service_date,
                    education_level_id=levels.get(period.student_id),
                    family_id=_family_id(family_by_student, period.student_id),
                )
                for period, _, service_date, _ in prepared
            ]
        )
    result: list[PeriodOutcome] = []
    for period, counts, service_date, base in prepared:
        level = levels.get(period.student_id)
        family = family_by_student.get(period.student_id)
        known = facts.get(period.enrollment_id, PeriodFacts())
        outcome = PeriodOutcome(
            outcome="billable",
            service_date=service_date,
            due_date_base=base,
            education_level_id=level,
            counts=counts,
        )
        if known.fee_posted:
            result.append(replace(outcome, outcome="posted_already"))
            continue
        if base < floor:
            result.append(replace(outcome, outcome="below_floor"))
            continue
        if period.is_group and known.session_charged:
            result.append(replace(outcome, outcome="session_priced"))
            continue
        price = await prices.base(
            fee_price_query(
                period,
                service_date=service_date,
                education_level_id=level,
                family_id=None if family is None else family.id,
            )
        )
        if price is None:
            missing: Outcome = "session_priced" if period.is_group else "no_price"
            result.append(replace(outcome, outcome=missing))
        elif known.other_fee_positive:
            result.append(replace(outcome, outcome="already_charged"))
        elif family is None:
            result.append(replace(outcome, outcome="no_billing_account"))
        else:
            result.append(replace(outcome, family=family, price=price))
    return result


def _family_id(
    family_by_student: dict[uuid.UUID, FamilyReference], student_id: uuid.UUID
) -> uuid.UUID | None:
    family = family_by_student.get(student_id)
    return None if family is None else family.id


async def _proration(
    session: AsyncSession, period: Period, counts: scheduling.SessionCounts
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """``(stored, used)``: the J8 snapshot (``None``: none) and the
    ``prorate`` argument of the quote (``None``: the full fee)."""
    if not period.is_group:
        total = await courses.cohort_session_count(session, period.owner_id)
        remaining = max(0, min(counts.remaining, total))
        return (remaining, total), (remaining, total)
    if counts.total is None:
        return None, None
    if counts.total == 0:
        return None, (0, 1)
    remaining = min(counts.remaining, counts.total)
    return (remaining, counts.total), (remaining, counts.total)


async def classify_enrollment_period(
    session: AsyncSession, period: Period, *, zone: str, floor: date
) -> PeriodOutcome:
    """B1 for one period (the hooks and the sweeper); a ``billable`` result
    carries the proration (J3, J8) and the quote priced on the prorated
    list amount (B3). No lock, no write."""
    [outcome] = await classify_periods(session, [period], zone=zone, floor=floor)
    if outcome.outcome != "billable":
        return outcome
    assert outcome.counts is not None and outcome.family is not None  # noqa: S101
    stored, used = await _proration(session, period, outcome.counts)
    quote = await billing.quote(
        session,
        fee_price_query(
            period,
            service_date=outcome.service_date,
            education_level_id=outcome.education_level_id,
            family_id=outcome.family.id,
        ),
        prorate=used,
    )
    if quote is None:
        return replace(outcome, outcome="no_price")
    return replace(outcome, quote=quote, proration=stored)


# --- Posting (B4) --------------------------------------------------------------------


async def post_enrollment_fee(
    session: AsyncSession, period: Period, outcome: PeriodOutcome
) -> FeeOutcome:
    """Post a ``billable`` period's fee in the caller's Unit of Work (the
    module docstring's posting order). Returns ``posted`` or
    ``already_posted``, or the outcome the re-check under the account lock
    found."""
    if outcome.outcome != "billable" or outcome.family is None or outcome.quote is None:
        raise ValueError("only a billable outcome with its quote is posted")
    account_id = await billing.ensure_billing_account(session, outcome.family.id)
    await billing.lock_billing_account(session, account_id)
    known = (await period_facts(session, [period])).get(
        period.enrollment_id, PeriodFacts()
    )
    if known.fee_posted:
        return "already_posted"
    if period.is_group and known.session_charged:
        return "session_priced"
    if known.other_fee_positive:
        return "already_charged"
    source = (
        {"group_enrollment_id": period.enrollment_id}
        if period.is_group
        else {"cohort_enrollment_id": period.enrollment_id}
    )
    try:
        posted = await billing.post_charge(
            session,
            billing.ChargeDraft.priced(
                outcome.quote,
                student_id=period.student_id,
                kind=period.kind,
                service_date=outcome.service_date,
                education_level_id=outcome.education_level_id,
                due_date_base=outcome.due_date_base,
                proration=outcome.proration,
                **source,
            ),
        )
    except billing.StudentHasNoFamilyError:
        return "no_billing_account"
    return "posted" if posted.created else "already_posted"


async def charge_period(session: AsyncSession, period: Period) -> FeeOutcome:
    """Classify and post one period in the caller's Unit of Work, after the
    caller's own scheduling or courses locks: ``billing_starts_on(lock=True)``
    (``FOR SHARE``; ``NULL`` → ``not_billable``) → the zone → B1 → B4."""
    floor = await billing.billing_starts_on(session, lock=True)
    if floor is None:
        return "not_billable"
    zone = await administration.get_center_time_zone(session)
    outcome = await classify_enrollment_period(session, period, zone=zone, floor=floor)
    if outcome.outcome == "billable":
        return await post_enrollment_fee(session, period, outcome)
    if outcome.outcome == "posted_already":
        return "already_posted"
    return cast(FeeOutcome, outcome.outcome)


# --- Removal credits and restorations (J4) -------------------------------------------


async def credit_removed_sessions(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    effective_at: datetime,
) -> int:
    """A group withdrawal's J4 credits (see the module docstring). Returns
    the number of adjustments created."""
    seats = await scheduling.group_sessions_after(
        session, group_id, student_id, effective_at
    )
    left = [seat.class_session_id for seat in seats if not seat.is_participant]
    states = await billing.session_charge_states(
        session, [(class_session_id, student_id) for class_session_id in left]
    )
    created = 0
    for class_session_id in left:
        state = states.get((class_session_id, student_id))
        if state is None or not state.net.is_positive():
            continue
        posted = await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=state.charge_id,
                kind="enrollment_removal",
                amount=-state.net,
                source_key=REMOVAL_KEY.format(
                    charge_id=state.charge_id,
                    enrollment_id=enrollment_id,
                    change="withdrawn",
                ),
            ),
        )
        created += int(posted.created)
    return created


async def restore_rejoined_sessions(
    session: AsyncSession,
    *,
    group_id: uuid.UUID,
    student_id: uuid.UUID,
    enrollment_id: uuid.UUID,
    effective_at: datetime,
) -> int:
    """A group re-enrollment's J4 restorations (see the module docstring).
    Returns the number of adjustments created."""
    seats = await scheduling.group_sessions_after(
        session, group_id, student_id, effective_at
    )
    joined = [seat.class_session_id for seat in seats if seat.is_participant]
    states = await billing.session_charge_states(
        session, [(class_session_id, student_id) for class_session_id in joined]
    )
    created = 0
    for class_session_id in joined:
        state = states.get((class_session_id, student_id))
        if state is None or not state.removals.is_negative():
            continue
        posted = await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=state.charge_id,
                kind="enrollment_removal",
                amount=-state.removals,
                source_key=REMOVAL_KEY.format(
                    charge_id=state.charge_id,
                    enrollment_id=enrollment_id,
                    change="enrolled",
                ),
            ),
        )
        created += int(posted.created)
    return created


# --- Candidates (D3) -----------------------------------------------------------------


def _candidates_query(
    kind: EnrollmentKind,
    *,
    starts_from: datetime,
    student_id: uuid.UUID | None = None,
) -> Select[Any]:
    """The periods of ``kind`` with no fee charge (D3; not executed), with
    the owner's name and the subject's name."""
    periods = period_columns(kind).subquery("periods")
    fees = billing.enrollment_fee_columns().subquery("fees")
    counted = scheduling.owner_session_columns().subquery("counted")
    subjects = catalog.subject_name_columns().subquery("subjects")
    is_group = kind == "group_enrollment"
    owner_column = counted.c.group_id if is_group else counted.c.cohort_id
    first_start = (
        select(func.min(counted.c.starts_at))
        .where(owner_column == periods.c.owner_id)
        .scalar_subquery()
    )
    names = (
        scheduling.group_name_columns() if is_group else courses.cohort_name_columns()
    ).subquery("owners")
    posted = (
        select(fees.c.charge_id)
        .where(fees.c.kind == kind, fees.c.enrollment_id == periods.c.enrollment_id)
        .exists()
    )
    query = (
        select(
            periods,
            names.c.name.label("owner_name"),
            subjects.c.name.label("subject_name"),
        )
        .join(names, names.c.id == periods.c.owner_id)
        .join(subjects, subjects.c.id == periods.c.subject_id)
        .where(
            ~posted,
            func.greatest(periods.c.enrolled_at, first_start) >= starts_from,
        )
        .order_by(periods.c.enrolled_at, periods.c.enrollment_id)
    )
    if is_group:
        items = billing.group_fee_item_columns().subquery("items")
        priced = (
            select(items.c.subject_id)
            .where(
                items.c.subject_id == periods.c.subject_id,
                or_(items.c.group_id.is_(None), items.c.group_id == periods.c.owner_id),
            )
            .exists()
        )
        query = query.where(priced)
    if student_id is not None:
        query = query.where(periods.c.student_id == student_id)
    return query


@dataclass(frozen=True)
class Candidate:
    """A sweeper candidate: the period and the names the unbilled read
    shows."""

    period: Period
    owner_name: str = ""
    subject_name: str = ""


def _candidate(kind: EnrollmentKind, row: Any) -> Candidate:
    return Candidate(
        period=Period.of_row(kind, row),
        owner_name=row.owner_name,
        subject_name=row.subject_name,
    )


type Position = tuple[datetime, uuid.UUID]
"""A keyset position: ``(enrolled_at, enrollment_id)``."""


async def candidate_page(
    session: AsyncSession,
    kind: EnrollmentKind,
    *,
    floor: date,
    zone: str,
    after: Position | None = None,
    limit: int = PAGE_SIZE,
) -> tuple[list[Candidate], Position | None]:
    """One keyset page of ``kind`` candidates after ``after`` (one
    statement), and the position of the next page (``None`` after a short
    page)."""
    query = _candidates_query(kind, starts_from=floor_instant(floor, zone))
    if after is not None:
        columns = query.selected_columns
        query = query.where(
            tuple_(columns.enrolled_at, columns.enrollment_id) > tuple_(*after)
        )
    rows = (await session.execute(query.limit(limit))).all()
    page = [_candidate(kind, row) for row in rows]
    following: Position | None = None
    if len(rows) == limit:
        last = page[-1].period
        following = (last.enrolled_at, last.enrollment_id)
    return page, following


# --- The sweeper (D1-D4) -------------------------------------------------------------


async def post_one(
    session: AsyncSession,
    kind: EnrollmentKind,
    enrollment_id: uuid.UUID,
) -> FeeOutcome:
    """D4 for one candidate in the caller's Unit of Work: the owner row
    ``FOR SHARE``, the period re-read, then :func:`charge_period`."""
    period: Period | None
    if kind == "group_enrollment":
        found = await scheduling.group_enrollment_period(session, enrollment_id)
        if found is None:
            return "not_billable"
        if await scheduling.lock_group_for_billing(session, found.group_id) is None:
            return "not_billable"
        period = await _group_period(session, enrollment_id)
    else:
        cohort = await courses.cohort_enrollment_period(session, enrollment_id)
        if cohort is None:
            return "not_billable"
        if await courses.lock_cohort_for_billing(session, cohort.cohort_id) is None:
            return "not_billable"
        period = await _cohort_period(session, enrollment_id)
    if period is None:  # pragma: no cover - periods are never deleted
        return "not_billable"
    return await charge_period(session, period)


async def _group_period(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> Period | None:
    found = await scheduling.group_enrollment_period(session, enrollment_id)
    if found is None:
        return None
    return Period(
        kind="group_enrollment",
        enrollment_id=found.enrollment_id,
        owner_id=found.group_id,
        student_id=found.student_id,
        subject_id=found.subject_id,
        enrolled_at=found.enrolled_at,
        withdrawn_at=found.withdrawn_at,
    )


async def _cohort_period(
    session: AsyncSession, enrollment_id: uuid.UUID
) -> Period | None:
    found = await courses.cohort_enrollment_period(session, enrollment_id)
    if found is None:
        return None
    return Period(
        kind="cohort_enrollment",
        enrollment_id=found.enrollment_id,
        owner_id=found.cohort_id,
        student_id=found.student_id,
        subject_id=found.subject_id,
        enrolled_at=found.enrolled_at,
        withdrawn_at=found.withdrawn_at,
    )


async def group_period(session: AsyncSession, enrollment_id: uuid.UUID) -> Period:
    """The group period of a hook's change (it exists: the hook just wrote
    it)."""
    found = await _group_period(session, enrollment_id)
    if found is None:  # pragma: no cover - the hook's own row
        raise ValueError("unknown group enrollment")
    return found


async def cohort_period(session: AsyncSession, enrollment_id: uuid.UUID) -> Period:
    """The cohort period of a hook's change."""
    found = await _cohort_period(session, enrollment_id)
    if found is None:  # pragma: no cover - the hook's own row
        raise ValueError("unknown cohort enrollment")
    return found


@dataclass(frozen=True)
class EnrollmentChargeRunResult:
    """The counts of one run (also its log line)."""

    candidates: int = 0
    posted: int = 0
    already_posted: int = 0
    below_floor: int = 0
    already_charged: int = 0
    session_priced: int = 0
    no_billing_account: int = 0
    no_price: int = 0
    not_billable: int = 0
    """The re-check under the locks found billing off or the period gone."""
    failed: int = 0


async def run_post_enrollment_charges(
    settings: Settings, *, now: datetime | None = None
) -> EnrollmentChargeRunResult:
    """One run: every candidate of both kinds, one Unit of Work each.
    Returns the counts. ``now`` is accepted for runs by hand and tests (the
    #91 signature) and unused. Billing off: nothing is read, all counts are
    0."""
    del now
    engine = create_worker_engine(settings)
    outcomes: Counter[str] = Counter()
    candidates = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            floor = await billing.billing_starts_on(uow.session)
            zone = (
                ""
                if floor is None
                else await administration.get_center_time_zone(uow.session)
            )
        for kind in KINDS if floor is not None else ():
            after: Position | None = None
            pending = True
            while pending and floor is not None:
                async with UnitOfWork(sessionmaker) as uow:
                    page, after = await candidate_page(
                        uow.session, kind, floor=floor, zone=zone, after=after
                    )
                pending = after is not None
                for candidate in page:
                    candidates += 1
                    period = candidate.period
                    try:
                        async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                            outcome = await post_one(
                                uow.session, kind, period.enrollment_id
                            )
                    except Exception as exc:  # noqa: BLE001 - one item never blocks the rest
                        failed += 1
                        logger.error(
                            "Posting an enrollment charge failed",
                            extra={
                                "kind": kind,
                                "enrollment_id": str(period.enrollment_id),
                                "student_id": str(period.student_id),
                                "error": type(exc).__name__,
                            },
                        )
                        continue
                    outcomes[outcome] += 1
    finally:
        await engine.dispose()
    result = EnrollmentChargeRunResult(
        candidates=candidates,
        posted=outcomes["posted"],
        already_posted=outcomes["already_posted"],
        below_floor=outcomes["below_floor"],
        already_charged=outcomes["already_charged"],
        session_priced=outcomes["session_priced"],
        no_billing_account=outcomes["no_billing_account"],
        no_price=outcomes["no_price"],
        not_billable=outcomes["not_billable"],
        failed=failed,
    )
    logger.info(
        "Posted enrollment charges",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


# --- Unbilled enrollments (F2) -------------------------------------------------------


def _record(id_: uuid.UUID, name: str) -> UnbilledRecord:
    return UnbilledRecord(id=id_, name=name)


async def list_unbilled_enrollments(
    session: AsyncSession,
    *,
    reasons: Collection[UnbilledReason] = (),
    kinds: Collection[EnrollmentKind] = (),
    student_id: uuid.UUID | None = None,
    page: OffsetParams,
) -> OffsetPage[UnbilledEnrollmentItem]:
    """The candidates the sweeper skips as ``no_billing_account`` or
    ``no_price`` (``reasons``/``kinds``: only those; empty = all),
    optionally one student's, by service date, student name and enrollment
    id. Billing off: an empty page. No lock, no write."""
    empty = OffsetPage[UnbilledEnrollmentItem](
        items=[], total=0, limit=page.limit, offset=page.offset
    )
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return empty
    zone = await administration.get_center_time_zone(session)
    wanted_kinds = [k for k in KINDS if not kinds or k in kinds]
    candidates: list[Candidate] = []
    for kind in wanted_kinds:
        rows = (
            await session.execute(
                _candidates_query(
                    kind,
                    starts_from=floor_instant(floor, zone),
                    student_id=student_id,
                )
            )
        ).all()
        candidates.extend(_candidate(kind, row) for row in rows)
    periods = [c.period for c in candidates]
    student_ids = {p.student_id for p in periods}
    students = await families.student_level_references(session, student_ids)
    family_by_student = await families.families_of_students(session, student_ids)
    classified = await classify_periods(
        session,
        periods,
        zone=zone,
        floor=floor,
        prefetch=True,
        family_by_student=family_by_student,
        students=students,
    )
    wanted = set(reasons) or set(UNBILLED_REASONS)

    def _name(student: uuid.UUID) -> str:
        reference = students.get(student)
        return "" if reference is None else reference.display_name

    unbilled = sorted(
        (
            (candidate, result)
            for candidate, result in zip(candidates, classified, strict=True)
            if result.outcome in wanted
        ),
        key=lambda pair: (
            pair[1].service_date,
            _name(pair[0].period.student_id).casefold(),
            pair[0].period.enrollment_id,
        ),
    )
    window = unbilled[page.offset : page.offset + page.limit]
    level_names = await catalog.education_level_references(
        session,
        {r.education_level_id for _, r in window if r.education_level_id is not None},
    )
    items: list[UnbilledEnrollmentItem] = []
    for candidate, result in window:
        period = candidate.period
        level = (
            None
            if result.education_level_id is None
            else level_names.get(result.education_level_id)
        )
        family = family_by_student.get(period.student_id)
        owner = _record(period.owner_id, candidate.owner_name)
        items.append(
            UnbilledEnrollmentItem(
                kind=period.kind,
                enrollment_id=period.enrollment_id,
                enrolled_at=period.enrolled_at,
                withdrawn_at=period.withdrawn_at,
                service_date=result.service_date,
                student=_record(period.student_id, _name(period.student_id)),
                family=None if family is None else _record(family.id, family.name),
                education_level=(
                    None if level is None else _record(level.id, level.name)
                ),
                subject=_record(period.subject_id, candidate.subject_name),
                group=owner if period.is_group else None,
                cohort=None if period.is_group else owner,
                reason=cast(UnbilledReason, result.outcome),
            )
        )
    return OffsetPage[UnbilledEnrollmentItem](
        items=items, total=len(unbilled), limit=page.limit, offset=page.offset
    )
