"""Work and corrections after lock (#111; spec §3, §7, W3.5; ADR-0017 E11 and its
#111 notes; data-model §11.9, §13, §15).

A finalized period never changes. Work dated inside one that reaches payroll
after its finalize, and a later revision of work it paid, become
``payroll_adjustments`` posted by ``payroll:post_adjustments``
(:mod:`app.workflows.payroll_adjustment_tasks`, every 15 minutes) into the
tutor's earliest ``open`` period, each shown there by one ``adjustment`` line
that a run never rebuilds. Nothing else queues the job (#111 E10).

**What it pays** (:func:`select_candidates`, read with no lock, composed from the
unexecuted ``SELECT`` s of ``academics``, ``scheduling`` and ``payroll``,
ADR-0002):

- ``late_approval`` of a closeout: the session's current revision is dated
  inside a **finalized** period of its tutor, with no session-sourced line, not
  settled (no adjustment names the session, #111 E7) and no adjustment with its
  key ``approval:<approval_id>``.
- ``late_approval`` of a family ``late`` or ``no_show`` cancellation under a
  paying rule (#443 D8), keyed ``cancellation:<class_session_id>``.
- ``hours_correction`` (#111 E2): a session paid in a finalized way (a finalized
  session line, or a ``late_approval``) whose current revision neither names. It
  is the current revision priced on the origin's snapshot, minus what was paid so
  far, keyed ``approval:<approval_id>``.

Amounts come from the same pure functions as a line (pm-107), a
``late_approval`` on the service date's basis with #111 E4's salaried dates, an
``hours_correction`` on the origin's snapshot. A zero, no basis or a covered
result writes nothing, and is worked out again on the next run.

**Never paid here:** work before the tutor's first period (it is in no finalized
period, so nothing selects it; #109 pays it with a one-time item), work before
``payroll_starts_on`` or while it is ``NULL``, work in an ``open`` or
``prepared`` period (``recalculate`` and drift own it), and a cohort priced
``per_course`` or an ``hours_correction`` of a ``per_course`` origin (M7-08d):
those are counted in ``skipped_course``.

**One Unit of Work per adjustment** (:func:`post_one`, ``actor_id=None``, in the
lock order of data-model §15): the advisory lock, only when a period must be
opened (:func:`~app.modules.payroll.service.open_period_for_adjustment`) → the
target period ``FOR UPDATE``, re-read, the next open one if its status changed
(a ``prepared`` period is never a target, #111 E6) → the versions ``FOR KEY
SHARE`` → the facts re-read and **the amount computed after the lock** → the
adjustment in a savepoint (a key or first-payment conflict counts as done) → its
line. A failure rolls back that adjustment only, is logged with ids and the
error class, and the run goes on.

Log lines carry ids and counts, never an amount, a rate or a reason (#111 E9).

**Statements:** :func:`select_candidates` costs a fixed number whatever the
number of finalized periods, sessions or tutors (#111 B7), and :func:`post_one`
one fixed number per adjustment.

**Run by hand** (:func:`run_post_adjustments` builds its own direct engine and
disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.payroll_adjustments import run_post_adjustments as run; \\
    print(asyncio.run(run(get_settings())))"
"""

import logging
import uuid
from collections import Counter
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Final, Literal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.payroll import service as payroll
from app.modules.payroll.schemas import PayrollPeriodFacts
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import floor_instant, local_date
from app.workflows.payroll_calculation import (
    COURSE,
    first_session_dates,
    salary_covers,
)

logger = logging.getLogger(__name__)

OPEN: Final = "open"
TARGET_TRIES: Final = 10
"""A target that stops being ``open`` under the lock is skipped for the next
one; each skip is a period that was prepared meanwhile, so this is a cap, never
reached."""

type Source = Literal["closeout", "cancellation", "correction"]
type Outcome = Literal["posted", "nothing", "already", "skipped_course"]


@dataclass(frozen=True)
class Candidate:
    """Work after lock that owes something, as the selection saw it."""

    source: Source
    class_session_id: uuid.UUID
    tutor_user_id: uuid.UUID


@dataclass(frozen=True)
class Selection:
    """What one read of the facts found."""

    candidates: int
    """Late work that owes something (with a basis, under a paying rule, not
    covered), per-course work skipped, and every unpaid revision, a zero
    difference included (#111 B6)."""
    to_post: list[Candidate]
    skipped_course: int


@dataclass(frozen=True)
class _Found:
    candidate: Candidate
    draft: payroll.AdjustmentDraft | None
    skipped_course: bool = False


# --- The facts -----------------------------------------------------------------------


def _late_closeouts(starts_from: Any, only: uuid.UUID | None) -> Select[Any]:
    rows = academics.current_approval_columns(starts_from=starts_from).subquery()
    query = select(rows).where(
        payroll.has_no_session_line(rows.c.class_session_id),
        payroll.has_no_adjustment(rows.c.class_session_id),
        payroll.has_no_adjustment_keyed(payroll.keyed(payroll.APPROVAL_KEY, rows.c.id)),
    )
    return query if only is None else query.where(rows.c.class_session_id == only)


def _late_cancellations(starts_from: Any, only: uuid.UUID | None) -> Select[Any]:
    rows = scheduling.payable_cancellation_columns(starts_from=starts_from).subquery()
    query = select(rows).where(
        payroll.has_no_session_line(rows.c.class_session_id),
        payroll.has_no_adjustment(rows.c.class_session_id),
        payroll.has_no_adjustment_keyed(
            payroll.keyed(payroll.CANCELLATION_KEY, rows.c.class_session_id)
        ),
    )
    return query if only is None else query.where(rows.c.class_session_id == only)


def _corrections(starts_from: Any, only: uuid.UUID | None) -> Select[Any]:
    rows = academics.current_approval_columns(starts_from=starts_from).subquery()
    query = select(rows).where(
        payroll.paid_after_lock(rows.c.class_session_id),
        payroll.revision_unpaid(rows.c.class_session_id, rows.c.id),
    )
    return query if only is None else query.where(rows.c.class_session_id == only)


def _approved(row: Any, zone: str, makeups: Collection[uuid.UUID]) -> Any:
    return payroll.ApprovedSession(
        approval_id=row.id,
        class_session_id=row.class_session_id,
        class_kind=row.session_kind,
        service_date=local_date(row.session_starts_at, zone),
        approved_minutes=row.approved_minutes,
        all_absent=academics.all_marked_absent(row.attendance, len(row.attendance)),
        is_makeup=row.class_session_id in makeups,
    )


@dataclass(frozen=True)
class _Bases:
    """Each tutor's versions, and the rules."""

    terms: Mapping[uuid.UUID, Sequence[payroll.TermsBasisVersion]]
    rates: Mapping[uuid.UUID, Sequence[Any]]
    rules: Sequence[Any]
    covers: Mapping[uuid.UUID, Sequence[payroll.SalaryCover]]

    def of(self, tutor: uuid.UUID) -> payroll.PayBasis:
        return payroll.PayBasis(
            terms=self.terms.get(tutor, ()),
            rates=self.rates.get(tutor, ()),
            rules=self.rules,
            salary_covers=self.covers.get(tutor, ()),
        )


async def _bases(
    session: AsyncSession, tutors: Collection[uuid.UUID], *, lock: uuid.UUID | None
) -> _Bases:
    """The versions with no lock (the selection), or one tutor's ``FOR KEY
    SHARE`` under the target lock (:func:`post_one`). Three statements, four with a
    salaried tutor."""
    if lock is not None:
        terms = {lock: await payroll.terms_basis_versions(session, lock)}
        rates = {lock: await payroll.rate_versions(session, lock)}
        rules = await payroll.session_pay_rule_versions(session)
    else:
        terms = await payroll.terms_basis_versions_of_tutors(session, tutors)
        rates = await payroll.rate_versions_of_tutors(session, tutors)
        rules = await payroll.session_pay_rule_versions(session, lock=False)
    return _Bases(
        terms=terms,
        rates=rates,
        rules=rules,
        covers=await salary_covers(session, terms),
    )


async def _found(
    session: AsyncSession,
    *,
    floor: date,
    zone: str,
    only: Candidate | None = None,
) -> list[_Found]:
    """Every candidate with what it owes now, or ``only`` that one (under its
    target lock, with its tutor's versions ``FOR KEY SHARE``). One code path, so
    the run and the selection can't disagree."""
    starts_from = floor_instant(floor, zone)
    one = None if only is None else only.class_session_id
    wants = (
        {"closeout", "cancellation", "correction"} if only is None else {only.source}
    )
    closeouts = (
        (await session.execute(_late_closeouts(starts_from, one))).all()
        if "closeout" in wants
        else []
    )
    cancellations = (
        (await session.execute(_late_cancellations(starts_from, one))).all()
        if "cancellation" in wants
        else []
    )
    corrections = (
        (await session.execute(_corrections(starts_from, one))).all()
        if "correction" in wants
        else []
    )
    spans = await payroll.finalized_spans_of_tutors(
        session,
        {row.tutor_user_id for row in (*closeouts, *cancellations)},
    )

    def span_of(row: Any, starts_at: Any) -> payroll.FinalizedSpan | None:
        return payroll.finalized_span_of(
            spans.get(row.tutor_user_id, ()), local_date(starts_at, zone)
        )

    late_closeouts = [
        (row, span)
        for row in closeouts
        if (span := span_of(row, row.session_starts_at)) is not None
    ]
    late_cancellations = [
        (row, span)
        for row in cancellations
        if (span := span_of(row, row.starts_at)) is not None
    ]
    if not (late_closeouts or late_cancellations or corrections):
        return []
    paid = await payroll.paid_so_far(
        session, [row.class_session_id for row in corrections]
    )
    tutors = {
        *(row.tutor_user_id for row, _ in late_closeouts),
        *(row.tutor_user_id for row, _ in late_cancellations),
        *(item.tutor_user_id for item in paid.values()),
    }
    bases = await _bases(
        session, tutors, lock=None if only is None else only.tutor_user_id
    )
    makeups = await scheduling.makeup_session_ids(
        session,
        [row.class_session_id for row, _ in late_closeouts]
        + [row.class_session_id for row in corrections],
    )
    first_dates = await first_session_dates(
        session,
        [
            row.class_session_id
            for row, _ in late_closeouts
            if row.session_kind == COURSE
        ],
        zone,
    )
    found: list[_Found] = []
    for row, span in late_closeouts:
        candidate = Candidate("closeout", row.class_session_id, row.tutor_user_id)
        basis = bases.of(row.tutor_user_id)
        first = first_dates.get(row.class_session_id)
        if first is not None and payroll.course_rate(basis, first) is not None:
            found.append(_Found(candidate, None, skipped_course=True))
            continue
        work = _approved(row, zone, makeups)
        found.append(
            _Found(
                candidate,
                _late(basis, work, row.tutor_user_id, span, approval_id=row.id),
            )
        )
    for row, span in late_cancellations:
        candidate = Candidate("cancellation", row.class_session_id, row.tutor_user_id)
        work = payroll.PayableCancellation(
            class_session_id=row.class_session_id,
            class_kind=row.kind,
            service_date=local_date(row.starts_at, zone),
            scheduled_minutes=row.scheduled_minutes,
            classification=row.classification,
        )
        found.append(
            _Found(
                candidate,
                _late(bases.of(row.tutor_user_id), work, row.tutor_user_id, span),
            )
        )
    for row in corrections:
        origin = paid.get(row.class_session_id)
        if origin is None:  # pragma: no cover - paid_after_lock selected it
            continue
        candidate = Candidate("correction", row.class_session_id, origin.tutor_user_id)
        if origin.origin.rate_model == payroll.PER_COURSE:
            found.append(_Found(candidate, None, skipped_course=True))
            continue
        found.append(
            _Found(
                candidate,
                _correction(
                    bases.of(origin.tutor_user_id),
                    origin,
                    _approved(row, zone, makeups),
                ),
            )
        )
    return found


def _late(
    basis: payroll.PayBasis,
    work: Any,
    tutor: uuid.UUID,
    span: payroll.FinalizedSpan,
    *,
    approval_id: uuid.UUID | None = None,
) -> payroll.AdjustmentDraft | None:
    priced = payroll.late_approval_draft(basis, work)
    if priced is None:
        return None
    amount = payroll.owed(priced.amount, ())
    if amount is None:
        return None
    return payroll.AdjustmentDraft(
        kind=payroll.LATE_APPROVAL,
        tutor_user_id=tutor,
        amount=amount,
        source_key=(
            payroll.cancellation_key(work.class_session_id)
            if approval_id is None
            else payroll.approval_key(approval_id)
        ),
        class_session_id=work.class_session_id,
        priced_on=priced,
        approval_id=approval_id,
        origin_period_id=span.id,
    )


def _correction(
    basis: payroll.PayBasis,
    origin: payroll.PaidSession,
    work: payroll.ApprovedSession,
) -> payroll.AdjustmentDraft | None:
    priced = payroll.correction_draft(basis, origin.origin, work)
    if priced is None:
        return None
    amount = payroll.owed(priced.amount, origin.paid)
    if amount is None:
        return None
    return payroll.AdjustmentDraft(
        kind=payroll.HOURS_CORRECTION,
        tutor_user_id=origin.tutor_user_id,
        amount=amount,
        source_key=payroll.approval_key(work.approval_id),
        class_session_id=work.class_session_id,
        priced_on=priced,
        approval_id=work.approval_id,
        origin_line_id=origin.origin_line_id,
    )


async def select_candidates(
    session: AsyncSession, *, floor: date, zone: str
) -> Selection:
    """B3: every candidate, with no lock."""
    found = await _found(session, floor=floor, zone=zone)
    return Selection(
        candidates=sum(
            1
            for item in found
            if item.draft is not None
            or item.skipped_course
            or item.candidate.source == "correction"
        ),
        to_post=[item.candidate for item in found if item.draft is not None],
        skipped_course=sum(1 for item in found if item.skipped_course),
    )


# --- One adjustment ------------------------------------------------------------------


async def lock_target(
    session: AsyncSession,
    tutor_user_id: uuid.UUID,
    *,
    today: date,
    after: date | None = None,
) -> PayrollPeriodFacts:
    """The tutor's earliest ``open`` period ``FOR UPDATE``, re-read (#111 E6),
    starting after ``after`` if given (a carry-forward's target, M7-08c E3). A
    tutor with none gets one first, under the advisory lock (D3, D4). A tutor
    with no period at all has nowhere to open one: ``409 payroll_no_period``
    (M7-08c D2; the job never selects such a tutor)."""
    for _ in range(TARGET_TRIES):
        period_id = await payroll.earliest_open_period(
            session, tutor_user_id, after=after
        )
        if period_id is None:
            await payroll.lock_payroll_periods(session)
            await payroll.open_period_for_adjustment(
                session, tutor_user_id, today=today, after=after
            )
            period_id = await payroll.earliest_open_period(
                session, tutor_user_id, after=after
            )
            if period_id is None:
                raise payroll.PayrollNoPeriodError()
        period = await payroll.lock_period(session, period_id)
        if period.status == OPEN:
            return period
    raise RuntimeError("the target period kept changing")  # pragma: no cover


async def post_one(
    session: AsyncSession,
    candidate: Candidate,
    *,
    floor: date,
    zone: str,
    today: date,
) -> Outcome:
    """One adjustment in the caller's Unit of Work, in the order of the module
    docstring."""
    target = await lock_target(session, candidate.tutor_user_id, today=today)
    found = [
        item
        for item in await _found(session, floor=floor, zone=zone, only=candidate)
        if item.candidate == candidate
    ]
    if not found:
        return "nothing"
    [item] = found
    if item.skipped_course:
        return "skipped_course"
    if item.draft is None:
        return "nothing"
    posted = await payroll.post_adjustment(session, item.draft, target.id)
    return "already" if posted is None else "posted"


# --- The run -------------------------------------------------------------------------


@dataclass(frozen=True)
class PostAdjustmentsRunResult:
    """The counts of one run (also its log line). No amount."""

    candidates: int = 0
    posted: int = 0
    skipped_course: int = 0
    failed: int = 0


async def run_post_adjustments(settings: Settings) -> PostAdjustmentsRunResult:
    """One run: the selection, then one Unit of Work per adjustment. Returns the
    counts."""
    engine = create_worker_engine(settings)
    outcomes: Counter[str] = Counter()
    failed = 0
    selection = Selection(candidates=0, to_post=[], skipped_course=0)
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            floor = await payroll.payroll_starts_on(uow.session)
            zone = ""
            today: date | None = None
            if floor is not None:
                zone = await administration.get_center_time_zone(uow.session)
                today = await administration.center_today(uow.session)
                selection = await select_candidates(uow.session, floor=floor, zone=zone)
        for candidate in selection.to_post:
            assert floor is not None and today is not None  # noqa: S101 - set with it
            try:
                async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                    outcome = await post_one(
                        uow.session, candidate, floor=floor, zone=zone, today=today
                    )
            except Exception as exc:  # noqa: BLE001 - one adjustment never blocks the rest
                failed += 1
                logger.error(
                    "Posting a payroll adjustment failed",
                    extra={
                        "class_session_id": str(candidate.class_session_id),
                        "tutor_user_id": str(candidate.tutor_user_id),
                        "error": type(exc).__name__,
                    },
                )
                continue
            outcomes[outcome] += 1  # after the commit
    finally:
        await engine.dispose()
    result = PostAdjustmentsRunResult(
        candidates=selection.candidates,
        posted=outcomes["posted"],
        skipped_course=selection.skipped_course,
        failed=failed,
    )
    logger.info(
        "Posted payroll adjustments",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result
