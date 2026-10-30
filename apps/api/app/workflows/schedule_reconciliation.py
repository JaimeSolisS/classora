"""Reconciling session charges with schedule changes (#99 M6-14; spec §3,
§6 reconciliation, W4.5; ADR-0006 E1, E2, E3, E10; ADR-0005 D6;
``_docs/data-model.md`` §3.10, §3.22, §6).

A charged session can be cancelled, skipped for a closure (and brought
back), or given another duration by many scheduling paths (#55 cancel and
#62 approvals through it, #54 series edits and cancellations, #236
occurrence-conflict decisions, #232 closure changes and the nightly
materialization, the #54 session ``PATCH``). Instead of a hook in each, the
job ``billing:reconcile_session_charges``
(:mod:`app.workflows.schedule_reconciliation_tasks`, every 15 minutes, also
queued after commit by the routes that change sessions and at the end of
``scheduling:materialize_series``) compares every ``session`` charge with
its **committed** session and posts what is missing (Q1). Nothing is posted
inside a scheduling transaction, so a billing failure never rolls back a
schedule change, and a path added later is covered too. The seams
``on_session_cancelled`` and ``on_session_rescheduled`` stay billing
no-ops (M7-04 keeps them for tutor pay).

**Candidates** (:func:`candidate_page`; one statement per keyset page of
:data:`PAGE_SIZE` by ``(class_session_id, student_id)``, until a short
page): a participant of an ``individual`` or ``group`` session (#92
``ELIGIBLE_KINDS``; never ``course``) matching one of:

1. **cancellation:** ``cancelled``, ``first_confirmed_at`` set, no decision
   for the key and no package consumption of it;
2. **closure credit:** ``skipped_closure``, a charge with ``amount > 0``,
   ``net > 0`` and ``closure_total = 0``;
3. **closure restore:** ``confirmed``/``completed`` with ``closure_total <
   0``;
4. **repricing:** ``confirmed``/``completed``, ``amount > 0``,
   ``closure_total = 0`` and billed minutes ≠ the session's
   ``scheduled_minutes``.

**Per candidate, its own Unit of Work** (actor ``None``), in this lock order
(data-model §6): ``scheduling.service.lock_session_for_billing`` (the
session row ``FOR SHARE``; the kind, state and participant are checked
again → ``skipped_not_applicable``) → ``billing.service.billing_starts_on(
lock=True)`` (``FOR SHARE``) → the key's charge, and if there is one
``billing.service.lock_billing_account`` (``FOR UPDATE``, the **last**
lock) before its net, closure total, billed amount and minutes are read.
Package consumption (``consume_session_credit``) and ``post_charge`` take
that same account lock last. One session per Unit of Work, so the job can't
deadlock with scheduling writers (sessions ``FOR UPDATE``, never an
account) or the #92 hooks (sessions → account).

**The rules** (Q2-Q9):

- **Cancellation:** ``scheduling.service.cancellation_classification`` (an
  approved request's effective outcome at its ``created_at``; otherwise a
  center cancellation: ``excused``, makeup allowed, at ``cancelled_at``) →
  ``billing.service.cancellation_rule_at(classified_at)`` →
  ``cancellation_outcome`` (makeup → ``credit``). ``credit``: no charge →
  ``no_charge``; ``amount = 0`` → ``zero_charge``; ``net = 0`` →
  ``already_zero``; otherwise a ``cancellation_credit`` of ``−net``
  (``credited``; E5 releases allocations above the new net). ``charge``:
  a charge → its closure credit is restored and it is repriced if needed
  (``charge_stands``); no charge → a package-covered key consumes its units
  (``package_consumed``; ``short``/``no_package`` fall through), a
  tuition- or fee-priced group key stands (``charge_stands``, no charge),
  otherwise #91's price posts the charge (``charged``) once billing is on
  and the service date is on or after the floor. Billing off, before the
  floor, no family or no price is **transient**: no decision, retried.
  Then the decision row (one per key; a concurrent worker's → done).
- **Closure:** ``skipped_closure`` with a positive net and no closure
  credit → ``closure_credit`` of ``−net`` (key ``n`` odd); back to
  ``confirmed``/``completed`` (or a cancellation with a ``charge`` outcome)
  → ``closure_credit`` of minus the closure total (``n`` even). The credit
  stays while the session is ``unassigned``/``offered``/``conflict``.
- **Repricing** (``closure_total = 0``, ``amount > 0``): target =
  ``amount`` for the charge's own minutes (``original``), else
  ``base_price`` for the new minutes on the charge's service date and level
  with the charge's discount snapshot re-applied in position order
  (``catalog``), else ``amount`` pro rata (``pro_rata``); ``delta = target −
  (amount + Σ duration_change)``, clamped so the net never goes below
  ``0.00``; one ``charge_duration_repricings`` row per session version
  (``delta 0.00`` → no adjustment).
- **Never adjusted:** zero charges, charges of non-participants (#92 owns
  withdrawals), non-``session`` charges, course sessions, substitutions,
  attendance and closeout (``actual_minutes``). A reschedule that keeps the
  minutes posts nothing.

A failing item rolls back, is logged with its ids and the error class only,
and is retried on the next run; the other items continue. Logs carry counts
only.

**Run by hand** (:func:`run_reconcile_session_charges` builds its own direct
engine and disposes it)::

    uv run python -c "import asyncio; from app.core.config import get_settings; \\
    from app.workflows.schedule_reconciliation import \\
    run_reconcile_session_charges as run; print(asyncio.run(run(get_settings())))"
"""

import logging
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import Select, and_, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.db import create_sessionmaker
from app.core.jobs import create_worker_engine
from app.core.money import Money
from app.core.uow import UnitOfWork
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.workflows.charge_pricing import PriceMemo, local_date
from app.workflows.session_charges import (
    ELIGIBLE_KINDS,
    Candidate,
    classify,
    group_pricing,
    price_query,
)

__all__ = [
    "PAGE_SIZE",
    "ReconciliationRunResult",
    "candidate_page",
    "reconcile_one",
    "repricing_target",
    "run_reconcile_session_charges",
]

logger = logging.getLogger(__name__)

PAGE_SIZE: Final = 200
"""Candidates per keyset page (D2)."""

CANCELLED: Final = "cancelled"
SKIPPED_CLOSURE: Final = "skipped_closure"
CHARGED_STATES: Final = ("confirmed", "completed")

type Event = Literal[
    "cancellation_credited",
    "cancellation_charge_stands",
    "cancellation_charged",
    "cancellation_package_consumed",
    "cancellation_no_charge",
    "closure_credited",
    "closure_restored",
    "repriced",
    "repriced_zero",
    "skipped_transient",
    "skipped_not_applicable",
]
NOT_APPLICABLE: Final[list[Event]] = ["skipped_not_applicable"]
TRANSIENT: Final[list[Event]] = ["skipped_transient"]
RESULT_EVENTS: Final[dict[str, Event]] = {
    "credited": "cancellation_credited",
    "no_charge": "cancellation_no_charge",
    "zero_charge": "cancellation_no_charge",
    "already_zero": "cancellation_no_charge",
    "charge_stands": "cancellation_charge_stands",
    "charged": "cancellation_charged",
    "package_consumed": "cancellation_package_consumed",
}


# --- Candidates (D2) -----------------------------------------------------------------


def _same_key(table: Any, sessions: Any) -> Any:
    return and_(
        table.c.class_session_id == sessions.c.class_session_id,
        table.c.student_id == sessions.c.student_id,
    )


def _candidates_query() -> Select[Any]:
    """The candidate keys of the module docstring (not executed)."""
    sessions = scheduling.reconciliation_session_columns(kinds=ELIGIBLE_KINDS).subquery(
        "sessions"
    )
    charges = billing.session_charge_reconciliation_columns().subquery("charges")
    decisions = billing.cancellation_decision_key_columns().subquery("decisions")
    consumed = billing.consumed_key_columns().subquery("consumed")
    zero = Money.zero()
    decided = select(decisions.c.class_session_id).where(_same_key(decisions, sessions))
    used = select(consumed.c.class_session_id).where(_same_key(consumed, sessions))
    charged = sessions.c.state.in_(CHARGED_STATES)
    return (
        select(sessions.c.class_session_id, sessions.c.student_id)
        .outerjoin(charges, _same_key(charges, sessions))
        .where(
            or_(
                and_(
                    sessions.c.state == CANCELLED,
                    sessions.c.first_confirmed_at.is_not(None),
                    ~decided.exists(),
                    ~used.exists(),
                ),
                and_(
                    sessions.c.state == SKIPPED_CLOSURE,
                    charges.c.amount > zero,
                    charges.c.net > zero,
                    charges.c.closure_total == zero,
                ),
                and_(charged, charges.c.closure_total < zero),
                and_(
                    charged,
                    charges.c.amount > zero,
                    charges.c.closure_total == zero,
                    charges.c.billed_minutes != sessions.c.scheduled_minutes,
                ),
            )
        )
        .order_by(sessions.c.class_session_id, sessions.c.student_id)
    )


type Key = tuple[uuid.UUID, uuid.UUID]
"""A candidate key and keyset position: ``(class_session_id, student_id)``."""


async def candidate_page(
    session: AsyncSession, *, after: Key | None = None, limit: int = PAGE_SIZE
) -> tuple[list[Key], Key | None]:
    """One keyset page of candidate keys after ``after`` (one statement) and
    the position of the next page (``None`` after a short page)."""
    query = _candidates_query()
    if after is not None:
        columns = query.selected_columns
        query = query.where(
            tuple_(columns.class_session_id, columns.student_id) > tuple_(*after)
        )
    rows = (await session.execute(query.limit(limit))).all()
    page = [(row.class_session_id, row.student_id) for row in rows]
    return page, (page[-1] if len(page) == limit else None)


# --- Repricing (D6) ------------------------------------------------------------------


@dataclass(frozen=True)
class RepricingTarget:
    """The amount a charge should bill for the new minutes, and how it was
    found."""

    amount: Money
    method: Literal["original", "catalog", "pro_rata"]


def repricing_target(
    charge: billing.ReconciliationCharge,
    new_minutes: int,
    *,
    list_amount: Money | None,
    snapshot: tuple[billing.DiscountLine, ...] | list[billing.DiscountLine] = (),
) -> RepricingTarget:
    """Q2 (pure): the charge's own amount for its own minutes
    (``original``); else ``list_amount`` (the base price for the new
    minutes) with the charge's discount snapshot re-applied in position
    order (``catalog``); else the amount pro rata (``pro_rata``, rounded
    half away from zero)."""
    if new_minutes == charge.scheduled_minutes:
        return RepricingTarget(charge.amount, "original")
    if list_amount is not None:
        specs = [
            billing.DiscountSpec(
                discount_id=line.discount_id,
                kind=line.kind,
                name=line.name,
                percent=line.percent,
                fixed_amount=line.fixed_amount,
                created_at=charge.posted_at,
            )
            for line in sorted(snapshot, key=lambda line: line.position)
        ]
        lines = billing.apply_discount_chain(list_amount, specs)
        return RepricingTarget(
            list_amount - Money.total(line.amount for line in lines), "catalog"
        )
    return RepricingTarget(
        charge.amount.prorate(new_minutes, charge.scheduled_minutes), "pro_rata"
    )


def clamp_delta(
    target: Money, charge: billing.ReconciliationCharge
) -> tuple[Money, bool]:
    """``delta = target − billed``, raised to ``−net`` when the net would go
    below ``0.00`` (I1 never raises; Q2)."""
    delta = target - charge.billed_amount
    if (charge.net + delta).is_negative():
        return -charge.net, True
    return delta, False


async def _reprice(
    session: AsyncSession,
    locked: scheduling.BillableSession,
    charge: billing.ReconciliationCharge,
) -> Event | None:
    """D6 for a charge whose closure total is ``0.00``; ``None`` when there
    is nothing to do (a zero charge, same minutes, this version done)."""
    new = locked.scheduled_minutes
    if (
        not charge.amount.is_positive()
        or not charge.closure_total.is_zero()
        or new == charge.billed_minutes
        or await billing.has_repricing(session, charge.charge_id, locked.version)
    ):
        return None
    list_amount: Money | None = None
    snapshot: list[billing.DiscountLine] = []
    if new != charge.scheduled_minutes:
        base = await billing.base_price(
            session,
            billing.PriceQuery(
                student_id=charge.student_id,
                family_id=charge.family_id,
                charge_kind="session",
                class_type=locked.kind,  # type: ignore[arg-type]
                pricing_model="per_session",
                subject_id=locked.subject_id,
                on=charge.service_date,
                education_level_id=charge.education_level_id,
                duration_minutes=new,
                group_id=locked.group_id,
            ),
        )
        if base is not None:
            list_amount = base.list_amount
            snapshot = (
                await billing.charge_discount_lines(session, [charge.charge_id])
            ).get(charge.charge_id, [])
    target = repricing_target(charge, new, list_amount=list_amount, snapshot=snapshot)
    delta, clamped = clamp_delta(target.amount, charge)
    adjustment_id: uuid.UUID | None = None
    if not delta.is_zero():
        posted = await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=charge.charge_id,
                kind="duration_change",
                amount=delta,
                source_key=billing.DURATION_CHANGE_KEY.format(
                    charge_id=charge.charge_id, version=locked.version
                ),
            ),
        )
        adjustment_id = posted.adjustment_id
    recorded = await billing.record_repricing(
        session,
        billing.RepricingDraft(
            charge_id=charge.charge_id,
            class_session_id=locked.id,
            session_version=locked.version,
            previous_minutes=charge.billed_minutes,
            scheduled_minutes=new,
            target_amount=target.amount,
            delta_amount=delta,
            method=target.method,
            clamped=clamped,
            charge_adjustment_id=adjustment_id,
        ),
    )
    if not recorded:
        return None
    return "repriced" if adjustment_id is not None else "repriced_zero"


# --- Closures (D5) -------------------------------------------------------------------


async def _post_closure_credit(
    session: AsyncSession, charge: billing.ReconciliationCharge, amount: Money
) -> None:
    await billing.post_adjustment(
        session,
        billing.AdjustmentDraft(
            charge_id=charge.charge_id,
            kind="closure_credit",
            amount=amount,
            source_key=billing.CLOSURE_CREDIT_KEY.format(
                charge_id=charge.charge_id, n=charge.closure_count + 1
            ),
        ),
    )


async def _restore_and_reprice(
    session: AsyncSession,
    locked: scheduling.BillableSession,
    charge: billing.ReconciliationCharge,
) -> list[Event]:
    """A charge that stands (``confirmed``/``completed``, or cancelled with
    a ``charge`` outcome): restore its closure credit (Q3), then reprice
    (D6). The charge is read again after the restoration (same lock)."""
    events: list[Event] = []
    if charge.amount.is_positive() and charge.closure_total.is_negative():
        await _post_closure_credit(session, charge, -charge.closure_total)
        events.append("closure_restored")
        charge = await billing.reconciliation_charge(session, charge.charge_id)
    repriced = await _reprice(session, locked, charge)
    if repriced is not None:
        events.append(repriced)
    return events


# --- Cancellations (D4) --------------------------------------------------------------


async def _charge_cancelled(
    session: AsyncSession,
    locked: scheduling.BillableSession,
    student_id: uuid.UUID,
    *,
    floor: Any,
    zone: str,
) -> tuple[str, uuid.UUID | None] | None:
    """A ``charge`` outcome on an uncharged key (D4): ``(result,
    charge_id)``, or ``None`` when it is transient (retried)."""
    service_date = local_date(locked.starts_at, zone)
    candidate = Candidate(
        class_session_id=locked.id,
        student_id=student_id,
        subject_id=locked.subject_id,
        starts_at=locked.starts_at,
        ends_at=locked.ends_at,
        state=locked.state,
        scheduled_minutes=locked.scheduled_minutes,
        service_date=service_date,
        kind=locked.kind,
        group_id=locked.group_id,
    )
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
    if classified.outcome == "package_covered":
        # Q7, L5: package consumption ignores the floor.
        consumed = await billing.consume_session_credit(
            session,
            billing.SessionCreditClaim(
                class_session_id=locked.id,
                student_id=student_id,
                subject_id=locked.subject_id,
                service_date=service_date,
                scheduled_minutes=locked.scheduled_minutes,
            ),
        )
        if consumed.outcome in ("consumed", "already_consumed"):
            return "package_consumed", None
        if consumed.outcome == "charged":
            found = await billing.session_charge_of_key(session, locked.id, student_id)
            return "charge_stands", None if found is None else found[0]
        # short / no_package: the ordinary charge below.
    elif classified.outcome in ("tuition_priced", "enrollment_priced"):
        return "charge_stands", None
    if floor is None or service_date < floor or family is None:
        return None
    if candidate.group_id is not None:
        # #92 E3, #93 K6 under the family's account lock, as the sweeper.
        account_id = await billing.ensure_billing_account(session, family.id)
        await billing.lock_billing_account(session, account_id)
        groups = await group_pricing(session, [candidate])
        if groups.tuition_priced(candidate.key, service_date) or (
            await groups.enrollment_priced(
                candidate.key,
                zone=zone,
                education_level_id=level,
                family_id=family.id,
                prices=PriceMemo(session),
            )
        ):
            return "charge_stands", None
    quote = await billing.quote(session, price_query(candidate, level, family.id))
    if quote is None:
        return None
    try:
        posted = await billing.post_charge(
            session,
            billing.ChargeDraft.priced(
                quote,
                student_id=student_id,
                kind="session",
                class_session_id=locked.id,
                service_date=service_date,
                scheduled_minutes=locked.scheduled_minutes,
                education_level_id=level,
                due_date_base=service_date,
            ),
        )
    except billing.StudentHasNoFamilyError:
        return None
    except billing.SessionSettledByPackageError:
        return "package_consumed", None
    return ("charged" if posted.created else "charge_stands"), posted.charge_id


async def _cancellation(
    session: AsyncSession,
    locked: scheduling.BillableSession,
    student_id: uuid.UUID,
    charge: billing.ReconciliationCharge | None,
    *,
    floor: Any,
    zone: str,
) -> list[Event]:
    """D4 for a cancelled session (module docstring)."""
    if locked.first_confirmed_at is None or await billing.has_cancellation_decision(
        session, locked.id, student_id
    ):
        return NOT_APPLICABLE
    facts = await scheduling.cancellation_classification(session, locked.id)
    if facts is None:  # pragma: no cover - the locked row is cancelled
        return NOT_APPLICABLE
    rule = await billing.cancellation_rule_at(session, facts.classified_at)
    outcome = billing.cancellation_outcome(
        rule, facts.classification, facts.makeup_allowed
    )
    events: list[Event] = []
    charge_id = None if charge is None else charge.charge_id
    adjustment_id: uuid.UUID | None = None
    if outcome == "credit":
        if charge is None:
            result = "no_charge"
        elif not charge.amount.is_positive():
            result = "zero_charge"
        elif not charge.net.is_positive():
            result = "already_zero"
        else:
            posted = await billing.post_adjustment(
                session,
                billing.AdjustmentDraft(
                    charge_id=charge.charge_id,
                    kind="cancellation_credit",
                    amount=-charge.net,
                    source_key=billing.CANCELLATION_CREDIT_KEY.format(
                        charge_id=charge.charge_id
                    ),
                ),
            )
            adjustment_id = posted.adjustment_id
            result = "credited"
    elif charge is not None:
        events.extend(await _restore_and_reprice(session, locked, charge))
        result = "charge_stands"
    else:
        charged = await _charge_cancelled(
            session, locked, student_id, floor=floor, zone=zone
        )
        if charged is None:
            return TRANSIENT
        result, charge_id = charged
    recorded = await billing.record_cancellation_decision(
        session,
        billing.CancellationDecisionDraft(
            class_session_id=locked.id,
            student_id=student_id,
            initiated_by=facts.initiated_by,
            change_request_id=facts.change_request_id,
            classification=facts.classification,
            makeup_allowed=facts.makeup_allowed,
            classified_at=facts.classified_at,
            rule_id=rule.id,
            outcome=outcome,
            result=result,
            charge_id=charge_id,
            charge_adjustment_id=adjustment_id,
        ),
    )
    if not recorded:
        return NOT_APPLICABLE
    return [RESULT_EVENTS[result], *events]


# --- One candidate (D3) --------------------------------------------------------------


async def reconcile_one(
    session: AsyncSession,
    class_session_id: uuid.UUID,
    student_id: uuid.UUID,
    *,
    zone: str,
) -> list[Event]:
    """D3 for one key in the caller's Unit of Work (the lock order of the
    module docstring). Returns what was done."""
    locked = await scheduling.lock_session_for_billing(session, class_session_id)
    if (
        locked is None
        or locked.kind not in ELIGIBLE_KINDS
        or student_id not in locked.participant_ids
    ):
        return NOT_APPLICABLE
    floor = await billing.billing_starts_on(session, lock=True)
    found = await billing.session_charge_of_key(session, class_session_id, student_id)
    charge: billing.ReconciliationCharge | None = None
    if found is not None:
        await billing.lock_billing_account(session, found[1])
        charge = await billing.reconciliation_charge(session, found[0])
    if locked.state == CANCELLED:
        return await _cancellation(
            session, locked, student_id, charge, floor=floor, zone=zone
        )
    if charge is None:
        return NOT_APPLICABLE
    if locked.state == SKIPPED_CLOSURE:
        if (
            charge.amount.is_positive()
            and charge.net.is_positive()
            and charge.closure_total.is_zero()
        ):
            await _post_closure_credit(session, charge, -charge.net)
            return ["closure_credited"]
        return NOT_APPLICABLE
    if locked.state in CHARGED_STATES:
        return await _restore_and_reprice(session, locked, charge) or NOT_APPLICABLE
    return NOT_APPLICABLE


# --- The run (D1) --------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationRunResult:
    """The counts of one run (also its log line)."""

    candidates: int = 0
    cancellation_credited: int = 0
    cancellation_charge_stands: int = 0
    cancellation_charged: int = 0
    cancellation_package_consumed: int = 0
    cancellation_no_charge: int = 0
    """``no_charge``, ``zero_charge`` and ``already_zero`` decisions."""
    closure_credited: int = 0
    closure_restored: int = 0
    repriced: int = 0
    repriced_zero: int = 0
    skipped_transient: int = 0
    skipped_not_applicable: int = 0
    failed: int = 0


async def run_reconcile_session_charges(
    settings: Settings, *, now: datetime | None = None
) -> ReconciliationRunResult:
    """One run: every candidate, one Unit of Work each. Returns the counts.

    ``now`` is accepted for runs by hand and tests (the #316 signature); the
    run doesn't depend on it (instants come from :mod:`app.core.clock`)."""
    del now
    engine = create_worker_engine(settings)
    events: Counter[str] = Counter()
    candidates = failed = 0
    try:
        sessionmaker = create_sessionmaker(engine)
        async with UnitOfWork(sessionmaker) as uow:
            zone = await administration.get_center_time_zone(uow.session)
        after: Key | None = None
        pending = True
        while pending:
            async with UnitOfWork(sessionmaker) as uow:
                page, after = await candidate_page(uow.session, after=after)
            pending = after is not None
            for class_session_id, student_id in page:
                candidates += 1
                try:
                    async with UnitOfWork(sessionmaker, actor_id=None) as uow:
                        done = await reconcile_one(
                            uow.session, class_session_id, student_id, zone=zone
                        )
                except Exception as exc:  # noqa: BLE001 - one item never blocks the rest
                    failed += 1
                    logger.error(
                        "Reconciling a session charge failed",
                        extra={
                            "class_session_id": str(class_session_id),
                            "student_id": str(student_id),
                            "error": type(exc).__name__,
                        },
                    )
                    continue
                events.update(done)
    finally:
        await engine.dispose()
    result = ReconciliationRunResult(
        candidates=candidates,
        failed=failed,
        **{
            name: events[name]
            for name in ReconciliationRunResult.__dataclass_fields__
            if name not in ("candidates", "failed")
        },
    )
    logger.info(
        "Reconciled session charges",
        extra={f"{name}_count": value for name, value in vars(result).items()},
    )
    return result
