"""Charges for individual sessions (#91 M6-06; spec §3, §6, W4.1, W4.2;
ADR-0006 E1, E2, E9, E10, E11; ``_docs/data-model.md`` §3.2, §3.9, §6).

Once staff set the billing start floor (``billing_settings.
billing_starts_on``, :mod:`app.modules.billing.settings`), every
``confirmed`` or ``completed`` **individual** session whose local date is on
or after the floor gets exactly one ``session`` charge per participant, at
the catalog price in effect on that local date. The periodic job
``billing:post_session_charges`` (:mod:`app.workflows.session_charge_tasks`,
every 15 minutes) posts them, **never inside a scheduling transaction**, so
a billing failure never rolls back a schedule change. Group per-session
charges are #92's (G1: it widens :data:`ELIGIBLE_KINDS` and
:func:`price_key`); course sessions are never charged per session.

**The rules, in one place** (:func:`classify`, used by the sweeper and the
unbilled list, so a listed item is exactly an item the sweeper skips with
that reason), for a candidate in this order:

1. ``package_covered``: ``billing.service.package_covered_keys`` returns its
   key (ADR-0006 E10; empty until #94) → no charge, not listed;
1a. ``tuition_priced`` (#93 K6): a **group** session whose participant's
   covering enrollment period has a linked tuition plan whose window
   (``starts_on`` to ``ends_on``, paused periods included) contains the
   session's local date → no charge, not listed (the plan prices it);
1b. ``enrollment_priced`` (#92 E2): the covering period has its fee, or is
   fee-priced (a period with a linked plan never is) → no charge, not
   listed;
2. ``no_billing_account``: the student has no family → no charge, listed;
3. ``no_price``: ``billing.service.base_price`` of the item's
   :class:`~app.modules.billing.schemas.PriceQuery` (``charge_kind
   'session'``, ``individual``, ``per_session``, the subject, ``on`` = the
   service date, the student's current level, ``duration`` = the scheduled
   minutes, the student and the family; #95 C2) is ``None``: no custom price
   of the student or the family, and no catalog price (or "not offered" at
   the deciding rank) → no charge, listed. A custom price makes the item
   billable even where the catalog has none;
4. ``billable``: the base price. A ``0.00`` price is a price (G5): a zero
   charge is posted (status ``void``), and the item is not listed. Discounts
   are not computed to classify.

**Service date** = the local date of ``starts_at`` in the center zone (read
once per run or read). **Due date** = the service date + the account's
``due_offset_days``. **Candidates** (one statement per keyset page of
:data:`PAGE_SIZE`, by ``(starts_at, class_session_id, student_id)``,
repeated until a short page, so unbillable items never starve new ones):
the participants of billable individual sessions starting at or after the
floor's local midnight, with no ``session`` charge for the key (an
anti-join on ``uq_charges_session_student``), service date ≥ floor.

**Per candidate, its own Unit of Work** (no actor), in the data-model §6
lock order:

1. ``scheduling.service.lock_session_for_billing`` (the session row ``FOR
   SHARE``): the kind, the state, the participant and the service date
   (from the locked ``starts_at``) are checked again → ``not_billable``;
2. ``billing.service.billing_starts_on(lock=True)`` (``FOR SHARE``): ``NULL``
   or a floor after the service date → ``not_billable``;
3. :func:`classify` (package seam → family → price);
4. ``billing.service.quote`` (#95 C1: the base price and the discounts;
   ``None`` → ``no_price``), then ``billing.service.post_charge`` with
   ``ChargeDraft.priced(quote, …)`` (the account row ``FOR UPDATE``, last;
   the ``charge_discounts`` snapshot only when the charge is created):
   ``created`` → ``posted``; already posted (a rerun, a concurrent worker)
   → ``already_posted``, a success; a family removed meanwhile
   (``StudentHasNoFamilyError``) → ``no_billing_account``.

Every scheduling writer that changes a session's state, time, duration or
participants takes the session row ``FOR UPDATE``, so the item waits for it
(or it waits for the item) and always posts from committed, current data.
A failing item rolls back, is logged with its ids and the error class only,
and the run continues; skipped items are candidates again on the next run
and are posted once their cause is fixed. A posted charge is never changed
by this job (a reschedule, a new duration, a new price version or a family
move keep it; corrections are #99).

**Run by hand** (:func:`run_post_session_charges` builds its own direct
engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.session_charges import run_post_session_charges as run; \\
    print(asyncio.run(run(get_settings())))"

**Unbilled items** (:func:`list_unbilled_items`, ``GET
/api/v1/billing/unbilled-items``): computed on read, nothing stored, no
lock, no write. Every candidate classified ``no_billing_account`` or
``no_price``; billing off → an empty page. At most seven statements (floor,
zone, candidates with their subject names, families, students, the custom
prices of every candidate in one batch (#95 C3), level names) plus the
catalog lookups, memoized per ``(subject, level, scheduled minutes, service
date)``.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Final, Literal, cast

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.workflows.charge_pricing import (
    Period,
    PeriodFacts,
    PriceKey,
    PriceMemo,
    fee_price_query,
    floor_instant,
    local_date,
    period_facts,
)
from app.workflows.session_charge_schemas import (
    BillableState,
    UnbilledClassType,
    UnbilledReason,
    UnbilledRecord,
    UnbilledSessionItem,
)

__all__ = [
    "ELIGIBLE_KINDS",
    "Candidate",
    "Classification",
    "GroupPricing",
    "PriceKey",
    "PriceMemo",
    "SessionChargeRunResult",
    "candidate_page",
    "classify",
    "floor_instant",
    "group_pricing",
    "list_unbilled_items",
    "local_date",
    "post_one",
    "price_key",
    "price_query",
    "run_post_session_charges",
]

logger = logging.getLogger(__name__)

ELIGIBLE_KINDS: Final = ("individual", "group")
"""The session kinds charged per session (#91 G1; ``group`` since #92 E1).
Course sessions are never charged per session."""
PAGE_SIZE: Final = 200
"""Candidates per keyset page (C4)."""
UNBILLED_REASONS: Final[tuple[UnbilledReason, ...]] = ("no_billing_account", "no_price")

type Outcome = Literal[
    "package_covered",
    "tuition_priced",
    "enrollment_priced",
    "no_billing_account",
    "no_price",
    "billable",
]
type ItemOutcome = Literal[
    "posted",
    "already_posted",
    "no_billing_account",
    "no_price",
    "package_covered",
    "tuition_priced",
    "enrollment_priced",
    "not_billable",
]


# --- Candidates ----------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One participant of a billable session, with its local service date."""

    class_session_id: uuid.UUID
    student_id: uuid.UUID
    subject_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    state: str
    scheduled_minutes: int
    service_date: date
    subject_name: str = ""
    kind: str = "individual"
    group_id: uuid.UUID | None = None
    """The group of a ``group`` session (#92 E1)."""
    group_name: str = ""

    @property
    def key(self) -> tuple[uuid.UUID, uuid.UUID]:
        return (self.class_session_id, self.student_id)


def _candidates_query(
    *, starts_from: datetime, student_id: uuid.UUID | None = None
) -> Select[Any]:
    """The billable participants with no ``session`` charge, with their
    subject's name (C4; not executed)."""
    participants = scheduling.billable_participant_columns(
        kinds=ELIGIBLE_KINDS, starts_from=starts_from, student_id=student_id
    ).subquery("participants")
    posted = billing.session_charge_key_columns().subquery("posted")
    subjects = catalog.subject_name_columns().subquery("subjects")
    groups = scheduling.group_name_columns().subquery("groups")
    already_posted = (
        select(posted.c.class_session_id)
        .where(
            posted.c.class_session_id == participants.c.class_session_id,
            posted.c.student_id == participants.c.student_id,
        )
        .exists()
    )
    return (
        select(
            participants,
            subjects.c.name.label("subject_name"),
            groups.c.name.label("group_name"),
        )
        .join(subjects, subjects.c.id == participants.c.subject_id)
        .outerjoin(groups, groups.c.id == participants.c.group_id)
        .where(~already_posted)
        .order_by(
            participants.c.starts_at,
            participants.c.class_session_id,
            participants.c.student_id,
        )
    )


def _candidate(row: Any, zone: str) -> Candidate:
    starts_at = row.starts_at.astimezone(UTC)
    return Candidate(
        class_session_id=row.class_session_id,
        student_id=row.student_id,
        subject_id=row.subject_id,
        starts_at=starts_at,
        ends_at=row.ends_at.astimezone(UTC),
        state=row.state,
        scheduled_minutes=row.scheduled_minutes,
        service_date=local_date(starts_at, zone),
        subject_name=row.subject_name,
        kind=row.kind,
        group_id=row.group_id,
        group_name=row.group_name or "",
    )


type Position = tuple[datetime, uuid.UUID, uuid.UUID]
"""A keyset position: ``(starts_at, class_session_id, student_id)``."""


async def candidate_page(
    session: AsyncSession,
    *,
    floor: date,
    zone: str,
    after: Position | None = None,
    limit: int = PAGE_SIZE,
) -> tuple[list[Candidate], Position | None]:
    """One keyset page of candidates after ``after`` (one statement), and
    the position to read the next page from (``None`` after a short page:
    the last one). Candidates whose local date is before the floor are left
    out."""
    query = _candidates_query(starts_from=floor_instant(floor, zone))
    if after is not None:
        columns = query.selected_columns
        query = query.where(
            tuple_(columns.starts_at, columns.class_session_id, columns.student_id)
            > tuple_(*after)
        )
    rows = (await session.execute(query.limit(limit))).all()
    page = [_candidate(row, zone) for row in rows]
    following: Position | None = None
    if len(rows) == limit:
        last = page[-1]
        following = (last.starts_at, last.class_session_id, last.student_id)
    return [c for c in page if c.service_date >= floor], following


# --- The classification (D1) ---------------------------------------------------------


def price_key(candidate: Candidate, education_level_id: uuid.UUID | None) -> PriceKey:
    """The one price key of a session charge: the session's class type
    (``individual``, or ``group`` with its ``group_id``, #92 E2), per
    session, the subject, the student's current level, the scheduled
    minutes and the service date."""
    return PriceKey(
        subject_id=candidate.subject_id,
        education_level_id=education_level_id,
        duration_minutes=candidate.scheduled_minutes,
        on=candidate.service_date,
        class_type=candidate.kind,
        group_id=candidate.group_id,
    )


def price_query(
    candidate: Candidate,
    education_level_id: uuid.UUID | None,
    family_id: uuid.UUID | None,
) -> billing.PriceQuery:
    """The :class:`~app.modules.billing.schemas.PriceQuery` of a session
    charge (#95 C1): :func:`price_key` plus the student, the family and the
    charge kind."""
    key = price_key(candidate, education_level_id)
    return billing.PriceQuery(
        student_id=candidate.student_id,
        family_id=family_id,
        charge_kind="session",
        class_type=cast(UnbilledClassType, key.class_type),
        pricing_model=key.pricing_model,
        subject_id=key.subject_id,
        on=key.on,
        education_level_id=key.education_level_id,
        duration_minutes=key.duration_minutes,
        group_id=key.group_id,
    )


@dataclass(frozen=True)
class Classification:
    """A candidate's outcome; ``price`` (the base price) only for
    ``billable``."""

    outcome: Outcome
    education_level_id: uuid.UUID | None = None
    price: billing.BasePrice | None = None


@dataclass(frozen=True)
class GroupPricing:
    """The enrollment periods covering group candidates (#92 E2), with
    their ledger facts and the windows of their linked tuition plans (#93
    K6). ``covering`` maps a candidate key to its period (none:
    session-priced)."""

    covering: Mapping[tuple[uuid.UUID, uuid.UUID], Period] = field(default_factory=dict)
    facts: Mapping[uuid.UUID, PeriodFacts] = field(default_factory=dict)
    plans: Mapping[uuid.UUID, Sequence[tuple[date, date | None]]] = field(
        default_factory=dict
    )

    def tuition_priced(
        self, key: tuple[uuid.UUID, uuid.UUID], service_date: date
    ) -> bool:
        """#93 K6: the covering period has a linked plan whose window
        contains ``service_date`` (paused periods included)."""
        period = self.covering.get(key)
        if period is None:
            return False
        return any(
            starts_on <= service_date and (ends_on is None or service_date <= ends_on)
            for starts_on, ends_on in self.plans.get(period.enrollment_id, ())
        )

    def fee_query(
        self,
        key: tuple[uuid.UUID, uuid.UUID],
        *,
        zone: str,
        education_level_id: uuid.UUID | None,
        family_id: uuid.UUID | None,
    ) -> billing.PriceQuery | None:
        """The fee query of the period covering ``key``, if any."""
        period = self.covering.get(key)
        if period is None:
            return None
        return fee_price_query(
            period,
            service_date=local_date(period.enrolled_at, zone),
            education_level_id=education_level_id,
            family_id=family_id,
        )

    async def enrollment_priced(
        self,
        key: tuple[uuid.UUID, uuid.UUID],
        *,
        zone: str,
        education_level_id: uuid.UUID | None,
        family_id: uuid.UUID | None,
        prices: PriceMemo,
    ) -> bool:
        """E2: the covering period has its fee charge, or is fee-priced
        (#92 B1 steps 3-4: no session charge of the student inside it, no
        linked tuition plan (#93 K6: such a period never gets a fee, so its
        sessions outside the plan's window are charged per session), and a
        ``group/full_course`` base price on its enrollment date)."""
        query = self.fee_query(
            key,
            zone=zone,
            education_level_id=education_level_id,
            family_id=family_id,
        )
        if query is None:
            return False
        period = self.covering[key]
        known = self.facts.get(period.enrollment_id, PeriodFacts())
        if known.fee_posted:
            return True
        if known.session_charged or known.tuition_linked:
            return False
        return await prices.base(query) is not None


async def group_pricing(
    session: AsyncSession, candidates: Sequence[Candidate]
) -> GroupPricing:
    """The :class:`GroupPricing` of the ``group`` candidates: their periods
    (one statement), the periods' ledger facts (one more) and the windows of
    their linked tuition plans (one more, #93 K6); nothing for no group
    candidate."""
    groups = [c for c in candidates if c.group_id is not None]
    if not groups:
        return GroupPricing()
    rows = await scheduling.covering_group_periods(
        session, {(c.group_id, c.student_id) for c in groups if c.group_id is not None}
    )
    covering: dict[tuple[uuid.UUID, uuid.UUID], Period] = {}
    for candidate in groups:
        for row in rows:
            if (
                row.group_id == candidate.group_id
                and row.student_id == candidate.student_id
                and scheduling.covers(row, candidate.starts_at)
            ):
                covering[candidate.key] = Period(
                    kind="group_enrollment",
                    enrollment_id=row.enrollment_id,
                    owner_id=row.group_id,
                    student_id=row.student_id,
                    subject_id=row.subject_id,
                    enrolled_at=row.enrolled_at,
                    withdrawn_at=row.withdrawn_at,
                )
                break
    unique = list({p.enrollment_id: p for p in covering.values()}.values())
    return GroupPricing(
        covering=covering,
        facts=await period_facts(session, unique),
        plans=await billing.linked_plan_windows(
            session, [p.enrollment_id for p in unique]
        ),
    )


async def classify(
    session: AsyncSession,
    candidates: Sequence[Candidate],
    *,
    families_by_student: Mapping[uuid.UUID, FamilyReference | None],
    levels: Mapping[uuid.UUID, uuid.UUID | None],
    prices: PriceMemo,
    zone: str,
    groups: GroupPricing | None = None,
) -> list[Classification]:
    """The rules of the module docstring, for each candidate (in order):
    package seam → tuition-priced group session (#93 K6) →
    enrollment-priced group session (#92 E2) → family → price. ``groups``
    is :func:`group_pricing` of the candidates (read here when not
    given)."""
    covered = await billing.package_covered_keys(
        session,
        [
            billing.CoverageQuery(
                class_session_id=c.class_session_id,
                student_id=c.student_id,
                subject_id=c.subject_id,
                service_date=c.service_date,
                scheduled_minutes=c.scheduled_minutes,
            )
            for c in candidates
        ],
    )
    if groups is None:
        groups = await group_pricing(session, candidates)
    result: list[Classification] = []
    for candidate in candidates:
        level = levels.get(candidate.student_id)
        family = families_by_student.get(candidate.student_id)
        if candidate.key in covered:
            result.append(Classification("package_covered", level))
        elif candidate.group_id is not None and groups.tuition_priced(
            candidate.key, candidate.service_date
        ):
            result.append(Classification("tuition_priced", level))
        elif candidate.group_id is not None and await groups.enrollment_priced(
            candidate.key,
            zone=zone,
            education_level_id=level,
            family_id=None if family is None else family.id,
            prices=prices,
        ):
            result.append(Classification("enrollment_priced", level))
        elif family is None:
            result.append(Classification("no_billing_account", level))
        else:
            price = await prices.base(
                price_query(candidate, level, family.id), price_key(candidate, level)
            )
            if price is None:
                result.append(Classification("no_price", level))
            else:
                result.append(Classification("billable", level, price))
    return result


# --- The sweeper (C2-C5) -------------------------------------------------------------


async def post_one(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    zone: str,
) -> ItemOutcome:
    """C5 for one candidate in the caller's Unit of Work (the lock order of
    the module docstring)."""
    locked = await scheduling.lock_session_for_billing(session, class_session_id)
    if (
        locked is None
        or locked.kind not in ELIGIBLE_KINDS
        or locked.state not in scheduling.BILLABLE_STATES
        or student_id not in locked.participant_ids
    ):
        return "not_billable"
    candidate = Candidate(
        class_session_id=locked.id,
        student_id=student_id,
        subject_id=locked.subject_id,
        starts_at=locked.starts_at,
        ends_at=locked.ends_at,
        state=locked.state,
        scheduled_minutes=locked.scheduled_minutes,
        service_date=local_date(locked.starts_at, zone),
        kind=locked.kind,
        group_id=locked.group_id,
    )
    floor = await billing.billing_starts_on(session, lock=True)
    if floor is None or candidate.service_date < floor:
        return "not_billable"
    family = await families.family_of_student(session, student_id)
    student = (await families.student_level_references(session, [student_id])).get(
        student_id
    )
    level = None if student is None else student.education_level_id
    [classified] = await classify(
        session,
        [candidate],
        families_by_student={student_id: family},
        levels={student_id: level},
        prices=PriceMemo(session),
        zone=zone,
    )
    if classified.outcome != "billable":
        return classified.outcome
    assert family is not None  # noqa: S101 - "billable" always has a family
    if candidate.group_id is not None:
        # #92 E3 (J2), #93 K6: under the family's account lock, the period's
        # fee, a linked tuition plan and this session charge can't coexist.
        account_id = await billing.ensure_billing_account(session, family.id)
        await billing.lock_billing_account(session, account_id)
        groups = await group_pricing(session, [candidate])
        if groups.tuition_priced(candidate.key, candidate.service_date):
            return "tuition_priced"
        if await groups.enrollment_priced(
            candidate.key,
            zone=zone,
            education_level_id=level,
            family_id=family.id,
            prices=PriceMemo(session),
        ):
            return "enrollment_priced"
    quote = await billing.quote(session, price_query(candidate, level, family.id))
    if quote is None:
        return "no_price"
    try:
        posted = await billing.post_charge(
            session,
            billing.ChargeDraft.priced(
                quote,
                student_id=student_id,
                kind="session",
                class_session_id=class_session_id,
                service_date=candidate.service_date,
                scheduled_minutes=candidate.scheduled_minutes,
                education_level_id=level,
                due_date_base=candidate.service_date,
            ),
        )
    except billing.StudentHasNoFamilyError:
        return "no_billing_account"
    return "posted" if posted.created else "already_posted"


@dataclass(frozen=True)
class SessionChargeRunResult:
    """The counts of one run (also its log line)."""

    candidates: int = 0
    posted: int = 0
    already_posted: int = 0
    no_billing_account: int = 0
    no_price: int = 0
    package_covered: int = 0
    tuition_priced: int = 0
    """Group sessions inside a linked tuition plan's window (#93 K6)."""
    enrollment_priced: int = 0
    """Group sessions of a fee-priced enrollment period (#92 E2)."""
    not_billable: int = 0
    """The re-check under the locks failed (C5 steps 1-2)."""
    failed: int = 0


async def run_post_session_charges(
    settings: Settings, *, now: datetime | None = None
) -> SessionChargeRunResult:
    """One run: every candidate, one Unit of Work each. Returns the counts.

    ``now`` is accepted for runs by hand and tests (the #316 signature); the
    run doesn't depend on it: a confirmed session is charged whenever the
    sweeper sees it, future sessions included, and ``posted_at`` comes from
    :mod:`app.core.clock`."""
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
        after: Position | None = None
        pending = floor is not None
        while pending and floor is not None:
            async with UnitOfWork(sessionmaker) as uow:
                page, after = await candidate_page(
                    uow.session, floor=floor, zone=zone, after=after
                )
            pending = after is not None
            for candidate in page:
                candidates += 1
                try:
                    async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                        outcome = await post_one(
                            uow.session,
                            candidate.class_session_id,
                            candidate.student_id,
                            zone=zone,
                        )
                except Exception as exc:  # noqa: BLE001 - one item never blocks the rest
                    failed += 1
                    logger.error(
                        "Posting a session charge failed",
                        extra={
                            "class_session_id": str(candidate.class_session_id),
                            "student_id": str(candidate.student_id),
                            "error": type(exc).__name__,
                        },
                    )
                    continue
                outcomes[outcome] += 1
    finally:
        await engine.dispose()
    result = SessionChargeRunResult(
        candidates=candidates,
        posted=outcomes["posted"],
        already_posted=outcomes["already_posted"],
        no_billing_account=outcomes["no_billing_account"],
        no_price=outcomes["no_price"],
        package_covered=outcomes["package_covered"],
        tuition_priced=outcomes["tuition_priced"],
        enrollment_priced=outcomes["enrollment_priced"],
        not_billable=outcomes["not_billable"],
        failed=failed,
    )
    logger.info(
        "Posted session charges",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result


# --- Unbilled items (D2-D4) ----------------------------------------------------------


def _record(id_: uuid.UUID, name: str) -> UnbilledRecord:
    return UnbilledRecord(id=id_, name=name)


def _family_id(
    family_by_student: Mapping[uuid.UUID, FamilyReference], student_id: uuid.UUID
) -> uuid.UUID | None:
    family = family_by_student.get(student_id)
    return None if family is None else family.id


async def list_unbilled_items(
    session: AsyncSession,
    *,
    reasons: Collection[UnbilledReason] = (),
    student_id: uuid.UUID | None = None,
    page: OffsetParams,
) -> OffsetPage[UnbilledSessionItem]:
    """The candidates the sweeper skips as ``no_billing_account`` or
    ``no_price`` (``reasons``: only those; empty = both), optionally one
    student's, by service date, start, student name, session id and student
    id. Billing off: an empty page. No lock, no write; at most seven
    statements, three more when group sessions are candidates (their
    covering periods, the periods' ledger facts, #92 E4, and their linked
    tuition plans, #93 K6), plus the memoized catalog lookups."""
    empty = OffsetPage[UnbilledSessionItem](
        items=[], total=0, limit=page.limit, offset=page.offset
    )
    floor = await billing.billing_starts_on(session)
    if floor is None:
        return empty
    zone = await administration.get_center_time_zone(session)
    rows = (
        await session.execute(
            _candidates_query(
                starts_from=floor_instant(floor, zone), student_id=student_id
            )
        )
    ).all()
    candidates = [
        candidate
        for candidate in (_candidate(row, zone) for row in rows)
        if candidate.service_date >= floor
    ]
    student_ids = {c.student_id for c in candidates}
    family_by_student = await families.families_of_students(session, student_ids)
    students = await families.student_level_references(session, student_ids)
    levels = {sid: ref.education_level_id for sid, ref in students.items()}
    groups = await group_pricing(session, candidates)
    prices = PriceMemo(session)
    fee_queries = [
        query
        for c in candidates
        if (
            query := groups.fee_query(
                c.key,
                zone=zone,
                education_level_id=levels.get(c.student_id),
                family_id=_family_id(family_by_student, c.student_id),
            )
        )
        is not None
    ]
    await prices.prefetch(
        [
            price_query(c, levels.get(c.student_id), family.id)
            for c in candidates
            if (family := family_by_student.get(c.student_id)) is not None
        ]
        + fee_queries
    )
    classified = await classify(
        session,
        candidates,
        families_by_student=family_by_student,
        levels=levels,
        prices=prices,
        zone=zone,
        groups=groups,
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
            pair[0].service_date,
            pair[0].starts_at,
            _name(pair[0].student_id).casefold(),
            pair[0].class_session_id,
            pair[0].student_id,
        ),
    )
    window = unbilled[page.offset : page.offset + page.limit]
    level_names = await catalog.education_level_references(
        session,
        {r.education_level_id for _, r in window if r.education_level_id is not None},
    )
    items: list[UnbilledSessionItem] = []
    for candidate, result in window:
        level = (
            None
            if result.education_level_id is None
            else level_names.get(result.education_level_id)
        )
        family = family_by_student.get(candidate.student_id)
        items.append(
            UnbilledSessionItem(
                class_session_id=candidate.class_session_id,
                starts_at=candidate.starts_at,
                ends_at=candidate.ends_at,
                service_date=candidate.service_date,
                state=cast(BillableState, candidate.state),
                scheduled_minutes=candidate.scheduled_minutes,
                subject=_record(candidate.subject_id, candidate.subject_name),
                student=_record(candidate.student_id, _name(candidate.student_id)),
                education_level=(
                    None if level is None else _record(level.id, level.name)
                ),
                family=None if family is None else _record(family.id, family.name),
                reason=cast(UnbilledReason, result.outcome),
                class_type=cast(UnbilledClassType, candidate.kind),
                group=(
                    None
                    if candidate.group_id is None
                    else _record(candidate.group_id, candidate.group_name)
                ),
            )
        )
    return OffsetPage[UnbilledSessionItem](
        items=items, total=len(unbilled), limit=page.limit, offset=page.offset
    )
