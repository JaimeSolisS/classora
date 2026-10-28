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
2. ``no_billing_account``: the student has no family → no charge, listed;
3. ``no_price``: ``billing.service.lookup_price(class_type='individual',
   pricing_model='per_session', subject, on=service date, the student's
   current level, duration=scheduled_minutes)`` is ``None`` (no price, or
   "not offered" at the deciding rank) → no charge, listed;
4. ``billable``: the price. A ``0.00`` price is a price (G5): a zero charge
   is posted (status ``void``), and the item is not listed.

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
4. ``billing.service.post_charge`` (the account row ``FOR UPDATE``, last):
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
``no_price``; billing off → an empty page. At most six statements (floor,
zone, candidates with their subject names, families, students, level names)
plus the price lookups, memoized per ``(subject, level, scheduled minutes,
service date)``.
"""

import logging
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

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
from app.workflows.session_charge_schemas import (
    BillableState,
    UnbilledReason,
    UnbilledRecord,
    UnbilledSessionItem,
)

logger = logging.getLogger(__name__)

ELIGIBLE_KINDS: Final = ("individual",)
"""The session kinds charged per session (#91 G1; #92 adds ``group``)."""
PAGE_SIZE: Final = 200
"""Candidates per keyset page (C4)."""
UNBILLED_REASONS: Final[tuple[UnbilledReason, ...]] = ("no_billing_account", "no_price")

type Outcome = Literal["package_covered", "no_billing_account", "no_price", "billable"]
type ItemOutcome = Literal[
    "posted",
    "already_posted",
    "no_billing_account",
    "no_price",
    "package_covered",
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

    @property
    def key(self) -> tuple[uuid.UUID, uuid.UUID]:
        return (self.class_session_id, self.student_id)


def local_date(instant: datetime, zone: str) -> date:
    """The center-local date of ``instant`` (ADR-0004)."""
    return instant.astimezone(ZoneInfo(zone)).date()


def floor_instant(floor: date, zone: str) -> datetime:
    """The instant of the floor's local midnight in the center zone."""
    return datetime.combine(floor, time(0), tzinfo=ZoneInfo(zone)).astimezone(UTC)


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
    already_posted = (
        select(posted.c.class_session_id)
        .where(
            posted.c.class_session_id == participants.c.class_session_id,
            posted.c.student_id == participants.c.student_id,
        )
        .exists()
    )
    return (
        select(participants, subjects.c.name.label("subject_name"))
        .join(subjects, subjects.c.id == participants.c.subject_id)
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


@dataclass(frozen=True)
class PriceKey:
    """The catalog lookup of a candidate (#91 C5 step 5). #92 adds the
    ``group`` kind and ``group_id``."""

    subject_id: uuid.UUID
    education_level_id: uuid.UUID | None
    duration_minutes: int
    on: date
    class_type: str = "individual"
    pricing_model: str = "per_session"


def price_key(candidate: Candidate, education_level_id: uuid.UUID | None) -> PriceKey:
    """The one price key of a session charge: individual, per session, the
    subject, the student's current level, the scheduled minutes and the
    service date."""
    return PriceKey(
        subject_id=candidate.subject_id,
        education_level_id=education_level_id,
        duration_minutes=candidate.scheduled_minutes,
        on=candidate.service_date,
    )


@dataclass
class PriceMemo:
    """Price lookups memoized per :class:`PriceKey` within one run or read."""

    session: AsyncSession
    found: dict[PriceKey, billing.PriceInEffect | None] = field(default_factory=dict)

    async def lookup(self, key: PriceKey) -> billing.PriceInEffect | None:
        if key not in self.found:
            self.found[key] = await billing.lookup_price(
                self.session,
                class_type=key.class_type,
                pricing_model=key.pricing_model,
                subject_id=key.subject_id,
                on=key.on,
                education_level_id=key.education_level_id,
                duration_minutes=key.duration_minutes,
            )
        return self.found[key]


@dataclass(frozen=True)
class Classification:
    """A candidate's outcome; ``price`` only for ``billable``."""

    outcome: Outcome
    education_level_id: uuid.UUID | None = None
    price: billing.PriceInEffect | None = None


async def classify(
    session: AsyncSession,
    candidates: Sequence[Candidate],
    *,
    families_by_student: Mapping[uuid.UUID, FamilyReference | None],
    levels: Mapping[uuid.UUID, uuid.UUID | None],
    prices: PriceMemo,
) -> list[Classification]:
    """The rules of the module docstring, for each candidate (in order):
    package seam → family → price."""
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
    result: list[Classification] = []
    for candidate in candidates:
        level = levels.get(candidate.student_id)
        if candidate.key in covered:
            result.append(Classification("package_covered", level))
        elif families_by_student.get(candidate.student_id) is None:
            result.append(Classification("no_billing_account", level))
        else:
            price = await prices.lookup(price_key(candidate, level))
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
    )
    if classified.outcome != "billable":
        return classified.outcome
    price = classified.price
    assert price is not None  # noqa: S101 - "billable" always carries its price
    try:
        posted = await billing.post_charge(
            session,
            billing.ChargeDraft(
                student_id=student_id,
                kind="session",
                class_session_id=class_session_id,
                service_date=candidate.service_date,
                scheduled_minutes=candidate.scheduled_minutes,
                price_version_id=price.price_version_id,
                education_level_id=level,
                list_amount=price.amount,
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
    id. Billing off: an empty page. No lock, no write; at most six
    statements plus the memoized price lookups."""
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
    classified = await classify(
        session,
        candidates,
        families_by_student=family_by_student,
        levels={sid: ref.education_level_id for sid, ref in students.items()},
        prices=PriceMemo(session),
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
    levels = await catalog.education_level_references(
        session,
        {r.education_level_id for _, r in window if r.education_level_id is not None},
    )
    items: list[UnbilledSessionItem] = []
    for candidate, result in window:
        level = (
            None
            if result.education_level_id is None
            else levels.get(result.education_level_id)
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
            )
        )
    return OffsetPage[UnbilledSessionItem](
        items=items, total=len(unbilled), limit=page.limit, offset=page.offset
    )
