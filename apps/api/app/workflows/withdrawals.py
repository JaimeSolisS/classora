"""Withdrawals: the preview and the confirm (#101 C1-C4; S1-S7; spec §1, §3,
§6 withdrawals, refunds and corrections, W4.4, W4.5; ADR-0002, ADR-0005,
ADR-0006 E5, E6, E8, E9, E10; ``_docs/data-model.md`` §3.23, §6).

A student leaves a group, a course cohort or a prepaid package: staff
preview what the withdrawal credits and confirm it as **account credit**
or a **refund**. The calculation rules are in
:mod:`app.modules.billing.withdrawals` (S1); this workflow joins
scheduling, courses, families and billing (ADR-0002). Nothing here
commits: each confirm route stores its response on its idempotency key in
the same transaction (#88).

**Subjects (S2).** A group enrollment, a cohort enrollment or a package.
An **active** enrollment is withdrawn by the confirm through the M4
workflow (``group_enrollment.withdraw_student`` /
``cohort_enrollment.withdraw_from_cohort``, ``at`` = now); one withdrawn
through the plain M4 route is **settled only** (``at`` = its
``withdrawn_at``; S3). At most one withdrawal per subject.

**Lines (B4).** The fee that pays for the period (its own, or #92 J5's
latest earlier fee of the same student and owner with a net above
``0.00``; never a fee another withdrawal credited already, so a J5 fee is
credited once); for a group, the posted ``tuition_period`` charges of a plan
linked to the enrollment whose period ends on or after the withdrawal's
local date; for a package, its ``package_purchase`` charge. **Never a
``session`` or ``manual`` charge** (no-double-credit boundary: #92 J4
credits the group session charges in the M4 hook, in the same
transaction, and the preview only shows them as ``automatic_credits``;
individual sessions are cancelled through the schedule, #99). A billable
fee or a due tuition period that isn't posted yet is a line with
``posting: "now"``: the confirm posts it first (S6), then credits it.

**Preview** (:func:`preview_withdrawal`, ``GET
/api/v1/billing/withdrawal-preview``): read-only, no lock, at most 20
statements whatever the line count (when nothing is posted "now"). A
blocking reason (``withdrawal_exists``, ``withdrawal_closeout_pending``,
``package_not_withdrawable``, ``student_has_no_family``,
``withdrawal_spans_accounts``) is still a ``200``. The ``fingerprint`` is
SHA-256 over the canonical JSON of the subject, the enrollment's status and
version, ``at`` to the minute, every line (charge, net, valid allocations,
counts, policy, computed credit, posting), the automatic credits and the
refundable cash (B7).

**Confirm** (:func:`confirm_withdrawal`), after the route's access rule,
reauthentication, idempotency key and body:

1. the subject (unknown → ``404 withdrawal_subject_not_found``);
2. an enrollment: ``schedule.manage`` (``403 permission_denied``; the
   confirm withdraws it);
3. the locks. Enrollment: the M4 sequence without the change
   (``scheduling.service.lock_group_enrollment_for_settlement`` /
   ``cohort_enrollment.lock_for_settlement``: students → zone ``FOR SHARE``
   → group/cohort ``FOR UPDATE`` → series ``FOR UPDATE`` → enrollment
   ``FOR UPDATE``). Package: only the account ``FOR UPDATE``;
4. the preview recomputed under those locks: a blocking reason → its
   ``409``; an active enrollment's ``version`` → ``409 version_conflict``;
   a different fingerprint → ``409 withdrawal_preview_stale`` with the
   fresh preview;
5. the overrides (unknown charge → ``422 credit_override_unknown_charge``;
   above the net → ``409 charge_net_negative``), the refund prediction
   (B2) and, for a refund, ``409 withdrawal_nothing_to_refund`` and the
   date/method checks (``422 payment_method_unavailable``,
   ``refunded_on_in_future``, ``refunded_on_before_payment``);
6. in a savepoint: an active enrollment is withdrawn (the M4 function: the
   same locks again, then the sessions; the group hook posts #92's J4
   credits under the account lock) → ``billing_settings FOR SHARE`` → the
   account ``FOR UPDATE`` (re-entrant, **last**) → the "now" fee and
   tuition periods posted → the real lines and J4 credits checked against
   the prediction → the ``withdrawals`` row → one ``withdrawal`` adjustment
   per line with an applied credit above ``0.00`` (``source_key
   withdrawal:<charge_id>:<withdrawal_id>``) → a package's ``refund`` units
   row → the real releases checked against B2 → for a refund, one
   ``refunds`` row per source payment, oldest ``received_on`` first. A
   mismatch rolls the savepoint back and answers ``409
   withdrawal_preview_stale`` with a fresh preview (a concurrent billing
   write between the locks and the account lock).

The account-credit outcome never allocates the released money (it stays
account credit, #96 ``…/allocations``). Unrelated older credit is never
refunded here (#100).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import Principal, RecordNotFoundError, ensure_permission
from app.core.errors import FieldError, VersionConflictError
from app.core.money import Money
from app.core.pagination import OffsetPage
from app.core.uow import UnitOfWork
from app.modules.academics import service as academics
from app.modules.administration import service as administration
from app.modules.billing import service as billing
from app.modules.courses import service as courses
from app.modules.families import service as families
from app.modules.scheduling import service as scheduling
from app.modules.scheduling.schemas import GroupWithdrawal
from app.workflows import cohort_enrollment, enrollment_charges, group_enrollment
from app.workflows.billing_accounts import get_summary
from app.workflows.charge_pricing import (
    Period,
    floor_instant,
    local_date,
    period_columns,
)
from app.workflows.cohort_schemas import CohortWithdrawal
from app.workflows.tuition_charges import (
    PlanFacts,
    period_outcomes,
    post_plan,
    price_query,
)
from app.workflows.withdrawal_schemas import (
    AutomaticCredit,
    LineBasis,
    PreviewBlocking,
    PreviewRecord,
    PreviewSessions,
    PreviewTotals,
    PreviewUnits,
    PreviewWarning,
    SubjectKind,
    WithdrawalConfirmed,
    WithdrawalCreditConfirm,
    WithdrawalDetail,
    WithdrawalItem,
    WithdrawalLine,
    WithdrawalPreview,
    WithdrawalRefundConfirm,
    WithdrawalSubject,
)

SCHEDULE_MANAGE = "schedule.manage"
INDIVIDUAL = ("individual",)

type Outcome = Literal["refund", "account_credit"]


@dataclass(frozen=True)
class _Line:
    """A line with what the confirm needs beyond the preview item."""

    item: WithdrawalLine
    account_id: uuid.UUID | None
    allocations: tuple[billing.AllocationFact, ...] = ()
    plan_id: uuid.UUID | None = None
    """The tuition plan of a ``tuition_period`` line."""


@dataclass
class _Computed:
    """A preview with the facts the confirm acts on."""

    preview: WithdrawalPreview
    lines: list[_Line]
    automatic: list[enrollment_charges.RemovalCredit]
    automatic_allocations: dict[uuid.UUID, list[billing.AllocationFact]]
    student_id: uuid.UUID
    family_id: uuid.UUID | None
    at: datetime
    zone: str
    period: Period | None = None
    status: Literal["active", "withdrawn"] | None = None
    version: int | None = None
    package: billing.PackageFacts | None = None
    now_fee: enrollment_charges.PeriodOutcome | None = None
    now_tuition_plans: list[uuid.UUID] = field(default_factory=list)


# --- Shared pieces -------------------------------------------------------------------


def _minute(at: datetime) -> str:
    return at.astimezone(UTC).replace(second=0, microsecond=0).isoformat()


def _fingerprint(
    *,
    subject: WithdrawalSubject,
    status: str | None,
    version: int | None,
    at: datetime,
    lines: Sequence[_Line],
    automatic: Sequence[enrollment_charges.RemovalCredit],
    automatic_allocations: Mapping[uuid.UUID, Sequence[billing.AllocationFact]],
    refundable: Money,
) -> str:
    def _allocations(rows: Sequence[billing.AllocationFact]) -> list[Any]:
        return sorted(
            (
                {
                    "allocation_id": a.allocation_id,
                    "payment_id": a.payment_id,
                    "amount": a.amount,
                }
                for a in rows
            ),
            key=lambda a: str(a["allocation_id"]),
        )

    return billing.fingerprint(
        {
            "subject": {"kind": subject.kind, "id": subject.id},
            "enrollment_status": status,
            "enrollment_version": version,
            "at": _minute(at),
            "lines": [
                {
                    "charge_id": line.item.charge_id,
                    "kind": line.item.kind,
                    "service_date": line.item.service_date,
                    "net": line.item.net,
                    "allocations": _allocations(line.allocations),
                    "denominator": line.item.basis.denominator,
                    "unused": line.item.basis.unused,
                    "policy": line.item.policy,
                    "computed_credit": line.item.computed_credit,
                    "posting": line.item.posting,
                }
                for line in lines
            ],
            "automatic_credits": [
                {
                    "charge_id": credit.charge_id,
                    "amount": credit.amount,
                    "allocations": _allocations(
                        automatic_allocations.get(credit.charge_id, ())
                    ),
                }
                for credit in automatic
            ],
            "refundable_cash": refundable,
        }
    )


def _freed(
    lines: Sequence[_Line],
    applied: Mapping[int, Money],
    automatic: Sequence[enrollment_charges.RemovalCredit],
    automatic_allocations: Mapping[uuid.UUID, Sequence[billing.AllocationFact]],
) -> billing.FreedCash:
    """B2 over the posted lines (with ``applied`` credits by line index) and
    the automatic credits (new net ``0.00``)."""
    after = [
        billing.ChargeAfter(
            charge_id=line.item.charge_id,
            new_net=line.item.net - applied[index],
            allocations=line.allocations,
        )
        for index, line in enumerate(lines)
        if line.item.charge_id is not None
    ]
    after.extend(
        billing.ChargeAfter(
            charge_id=credit.charge_id,
            new_net=Money.zero(),
            allocations=automatic_allocations.get(credit.charge_id, ()),
        )
        for credit in automatic
    )
    return billing.plan_freed_cash(after)


async def _record(
    session: AsyncSession, student_id: uuid.UUID
) -> tuple[PreviewRecord, PreviewRecord | None]:
    students = await families.student_references(session, [student_id])
    student = students.get(student_id)
    family = await families.family_of_student(session, student_id)
    return (
        PreviewRecord(
            id=student_id, name="" if student is None else student.display_name
        ),
        None if family is None else PreviewRecord(id=family.id, name=family.name),
    )


def _finish(
    session_preview: dict[str, Any],
    *,
    subject: WithdrawalSubject,
    lines: list[_Line],
    automatic: list[enrollment_charges.RemovalCredit],
    automatic_allocations: dict[uuid.UUID, list[billing.AllocationFact]],
    account_credit: Money,
    blocking: list[PreviewBlocking],
    warnings: list[PreviewWarning],
    status: str | None,
    version: int | None,
    at: datetime,
) -> WithdrawalPreview:
    computed = {i: line.item.computed_credit for i, line in enumerate(lines)}
    freed = _freed(lines, computed, automatic, automatic_allocations)
    credit = Money.total(line.item.computed_credit for line in lines)
    freed_lines = Money.total(
        freed.by_charge.get(line.item.charge_id, Money.zero())
        for line in lines
        if line.item.charge_id is not None
    )
    return WithdrawalPreview(
        subject=subject,
        lines=[line.item for line in lines],
        automatic_credits=[
            AutomaticCredit(
                charge_id=c.charge_id,
                class_session_id=c.class_session_id,
                amount=c.amount,
            )
            for c in automatic
        ],
        totals=PreviewTotals(
            credit=credit,
            reduces_debt=credit - freed_lines,
            refundable_cash=freed.total,
            automatic_credits=Money.total(c.amount for c in automatic),
            account_credit_after_credit=account_credit + freed.total,
        ),
        warnings=warnings,
        blocking=blocking,
        fingerprint=_fingerprint(
            subject=subject,
            status=status,
            version=version,
            at=at,
            lines=lines,
            automatic=automatic,
            automatic_allocations=automatic_allocations,
            refundable=freed.total,
        ),
        enrollment_status=cast(Any, status),
        enrollment_version=version,
        at=at,
        **session_preview,
    )


def _line(
    charge: billing.LineCharge | None,
    *,
    kind: str,
    service_date: date,
    service_period_end: date | None,
    net: Money,
    consumed: int,
    unused: int,
    denominator: int,
    open_ended: bool = False,
    plan_id: uuid.UUID | None = None,
) -> _Line:
    rule = billing.withdrawal_credit(net, unused, denominator, open_ended=open_ended)
    return _Line(
        item=WithdrawalLine(
            charge_id=None if charge is None else charge.charge_id,
            kind=cast(Any, kind),
            service_date=service_date,
            service_period_end=service_period_end,
            net=net,
            allocated=Money.zero() if charge is None else charge.allocated,
            basis=LineBasis(consumed=consumed, unused=unused, denominator=denominator),
            policy=rule.policy,
            computed_credit=rule.credit,
            posting="now" if charge is None else "posted",
        ),
        account_id=None if charge is None else charge.billing_account_id,
        allocations=() if charge is None else charge.allocations,
        plan_id=plan_id,
    )


# --- Enrollment subjects -------------------------------------------------------------


@dataclass(frozen=True)
class _EnrollmentState:
    period: Period
    version: int


async def _enrollment_state(
    session: AsyncSession, kind: SubjectKind, enrollment_id: uuid.UUID
) -> _EnrollmentState | None:
    """The period and its version (unlocked; re-read)."""
    if kind == "group_enrollment":
        found = await scheduling.group_enrollment_state(session, enrollment_id)
        if found is None:
            return None
        period = await enrollment_charges.group_period(session, enrollment_id)
        return _EnrollmentState(period=period, version=found.version)
    row = await courses.find_cohort_enrollment(session, enrollment_id)
    if row is None:
        return None
    period = await enrollment_charges.cohort_period(session, enrollment_id)
    return _EnrollmentState(period=period, version=row.version)


async def _fee_rows(session: AsyncSession, period: Period) -> list[Any]:
    """The fee charges of every period of the student in the same group or
    cohort, with the period's ``enrolled_at`` (one statement)."""
    periods = period_columns(period.kind).subquery("periods")
    fees = billing.enrollment_fee_columns().subquery("fees")
    rows = await session.execute(
        select(
            fees.c.charge_id,
            fees.c.enrollment_id,
            fees.c.net,
            fees.c.posted_at,
            periods.c.enrolled_at,
        )
        .join(periods, periods.c.enrollment_id == fees.c.enrollment_id)
        .where(
            fees.c.kind == period.kind,
            periods.c.owner_id == period.owner_id,
            periods.c.student_id == period.student_id,
        )
    )
    return list(rows)


def _fee_line_row(rows: Sequence[Any], period: Period) -> Any | None:
    """B4: the period's own fee, else the latest earlier period's fee with a
    net above ``0.00`` (J5)."""
    for row in rows:
        if row.enrollment_id == period.enrollment_id:
            return row
    earlier = [
        row
        for row in rows
        if row.enrollment_id != period.enrollment_id
        and row.enrolled_at <= period.enrolled_at
        and Money(row.net).is_positive()
    ]
    if not earlier:
        return None
    return max(earlier, key=lambda row: (row.posted_at, str(row.charge_id)))


async def _now_tuition(
    session: AsyncSession,
    plans: Sequence[billing.TuitionPlanRow],
    *,
    floor: date,
    withdrawal_day: date,
) -> list[tuple[billing.TuitionPlanRow, billing.Period, Money]]:
    """The due, unposted periods of the linked plans that the confirm posts
    now (S6): what ``tuition_charges.post_plan`` would post with ``today`` =
    the withdrawal's local date and the enrollment ending that day (the
    periods starting before it), with their quoted amount."""
    if not plans:
        return []
    found: list[tuple[billing.TuitionPlanRow, billing.Period, Money]] = []
    ids = billing.plan_ids_of(plans)
    pauses = await billing.plan_pauses(session, ids)
    posted = await billing.posted_period_starts(session, ids)
    for plan in plans:
        family = await families.family_of_student(session, plan.student_id)
        facts = PlanFacts(
            plan=plan,
            pauses=[p.window for p in pauses.get(plan.id, [])],
            posted=posted.get(plan.id, set()),
            withdrawn_on=withdrawal_day,
            current_family_id=None if family is None else family.id,
        )
        outcomes = [
            p
            for p, outcome in period_outcomes(
                facts, first=floor, today=withdrawal_day, floor=floor
            )
            if outcome == "priceable"
        ]
        if not outcomes:
            continue
        item = await billing.per_period_item(session, plan.service_price_id)
        if item is None:  # pragma: no cover - RESTRICT keeps the pinned item
            continue
        student = (
            await families.student_level_references(session, [plan.student_id])
        ).get(plan.student_id)
        level = None if student is None else student.education_level_id
        for period in outcomes:
            quote = await billing.quote(
                session, price_query(plan, item, period, education_level_id=level)
            )
            if quote is not None:
                found.append((plan, period, quote.amount))
    return found


async def _enrollment_preview(
    session: AsyncSession,
    kind: SubjectKind,
    state: _EnrollmentState,
    *,
    now: datetime,
) -> _Computed:
    period = state.period
    status: Literal["active", "withdrawn"] = (
        "active" if period.withdrawn_at is None else "withdrawn"
    )
    at = now if period.withdrawn_at is None else period.withdrawn_at
    subject = WithdrawalSubject(kind=kind, id=period.enrollment_id)
    zone = await administration.get_center_time_zone(session)
    floor = await billing.billing_starts_on(session)
    withdrawal_day = local_date(at, zone)
    existing = await billing.withdrawal_of_subject(session, kind, period.enrollment_id)
    schedules = await scheduling.owner_schedules(session, [period.owner])
    schedule = schedules[period.owner]
    own = schedule.counts(period.enrolled_at)
    after = schedule.counts(at)
    sessions = PreviewSessions(
        consumed=max(0, own.remaining - after.remaining),
        unused=after.remaining,
        consumed_minutes=max(0, own.remaining_minutes - after.remaining_minutes),
        unused_minutes=after.remaining_minutes,
    )
    fee_rows = await _fee_rows(session, period)
    fee_row = _fee_line_row(fee_rows, period)
    plans: list[billing.TuitionPlanRow] = []
    tuition_ids: list[uuid.UUID] = []
    if period.is_group:
        plans = await billing.list_plans(
            session, group_enrollment_ids=[period.enrollment_id]
        )
        tuition_ids = await billing.tuition_period_charge_ids(
            session, billing.plan_ids_of(plans), ends_from=withdrawal_day
        )
    charge_ids = [*([fee_row.charge_id] if fee_row is not None else []), *tuition_ids]
    charges = await billing.line_charges(session, charge_ids)
    credited = fee_row is not None and charges[fee_row.charge_id].withdrawal_credited
    warnings: list[PreviewWarning] = []
    lines: list[_Line] = []
    now_fee: enrollment_charges.PeriodOutcome | None = None
    open_ended = period.is_group and schedule.open_ended
    if fee_row is not None and not credited:
        charge = charges[fee_row.charge_id]
        fee_d = schedule.counts(fee_row.enrolled_at.astimezone(UTC)).remaining
        lines.append(
            _line(
                charge,
                kind=charge.kind,
                service_date=charge.service_date,
                service_period_end=None,
                net=charge.net,
                consumed=max(0, fee_d - after.remaining),
                unused=after.remaining,
                denominator=fee_d,
                open_ended=open_ended,
            )
        )
    elif fee_row is None and floor is not None and existing is None:
        outcome = await enrollment_charges.classify_enrollment_period(
            session, period, zone=zone, floor=floor
        )
        if outcome.outcome == "billable" and outcome.quote is not None:
            now_fee = outcome
            lines.append(
                _line(
                    None,
                    kind=period.kind,
                    service_date=outcome.service_date,
                    service_period_end=None,
                    net=outcome.quote.amount,
                    consumed=sessions.consumed,
                    unused=after.remaining,
                    denominator=own.remaining,
                    open_ended=open_ended,
                )
            )
            warnings.append(PreviewWarning(code="fee_posted_now", count=1))
    for charge_id in tuition_ids:
        charge = charges[charge_id]
        assert charge.service_period_end is not None  # noqa: S101 - a tuition charge
        window = (charge.service_date, charge.service_period_end)
        d = schedule.counts(period.enrolled_at, local_window=window, zone=zone)
        u = schedule.counts(at, local_window=window, zone=zone)
        lines.append(
            _line(
                charge,
                kind=charge.kind,
                service_date=charge.service_date,
                service_period_end=charge.service_period_end,
                net=charge.net,
                consumed=max(0, d.remaining - u.remaining),
                unused=u.remaining,
                denominator=d.remaining,
            )
        )
    now_plans: list[uuid.UUID] = []
    if period.is_group and floor is not None and existing is None:
        posted_now = await _now_tuition(
            session, plans, floor=floor, withdrawal_day=withdrawal_day
        )
        for plan, tperiod, amount in posted_now:
            now_plans.append(plan.id)
            if tperiod.end < withdrawal_day:
                continue
            window = (tperiod.start, tperiod.end)
            d = schedule.counts(period.enrolled_at, local_window=window, zone=zone)
            u = schedule.counts(at, local_window=window, zone=zone)
            lines.append(
                _line(
                    None,
                    kind="tuition_period",
                    service_date=tperiod.start,
                    service_period_end=tperiod.end,
                    net=amount,
                    consumed=max(0, d.remaining - u.remaining),
                    unused=u.remaining,
                    denominator=d.remaining,
                    plan_id=plan.id,
                )
            )
        if posted_now:
            warnings.append(
                PreviewWarning(code="tuition_posted_now", count=len(posted_now))
            )
    if (
        open_ended
        and lines
        and any(line.item.policy == "open_ended_fee" for line in lines)
    ):
        warnings.append(PreviewWarning(code="open_ended_fee", count=1))
    automatic: list[enrollment_charges.RemovalCredit] = []
    automatic_allocations: dict[uuid.UUID, list[billing.AllocationFact]] = {}
    if period.is_group and status == "active":
        automatic = await enrollment_charges.planned_removal_credits(
            session, group_id=period.owner_id, student_id=period.student_id, at=at
        )
        automatic_allocations = await billing.valid_allocations(
            session, [c.charge_id for c in automatic]
        )
    return await _close(
        session,
        subject=subject,
        student_id=period.student_id,
        lines=lines,
        automatic=automatic,
        automatic_allocations=automatic_allocations,
        existing=existing,
        warnings=warnings,
        status=status,
        version=state.version,
        at=at,
        zone=zone,
        extra={
            "enrolled_at": period.enrolled_at,
            "sessions": sessions,
            "units": None,
        },
        computed={
            "period": period,
            "status": status,
            "version": state.version,
            "now_fee": now_fee,
            "now_tuition_plans": sorted(set(now_plans)),
        },
    )


async def _close(
    session: AsyncSession,
    *,
    subject: WithdrawalSubject,
    student_id: uuid.UUID,
    lines: list[_Line],
    automatic: list[enrollment_charges.RemovalCredit],
    automatic_allocations: dict[uuid.UUID, list[billing.AllocationFact]],
    existing: uuid.UUID | None,
    warnings: list[PreviewWarning],
    status: str | None,
    version: int | None,
    at: datetime,
    zone: str,
    extra: dict[str, Any],
    computed: dict[str, Any],
    blocking: list[PreviewBlocking] | None = None,
    account_id: uuid.UUID | None = None,
    family: PreviewRecord | None = None,
) -> _Computed:
    """The account (B6), the blocking reasons and the totals."""
    blocking = list(blocking or [])
    if existing is not None:
        blocking.insert(
            0,
            PreviewBlocking(
                code="withdrawal_exists", details={"withdrawal_id": str(existing)}
            ),
        )
    student, current_family = await _record(session, student_id)
    family = family or current_family
    accounts = {line.account_id for line in lines if line.account_id is not None}
    if len(accounts) > 1:
        blocking.append(
            PreviewBlocking(
                code="withdrawal_spans_accounts",
                details={"billing_account_ids": sorted(str(a) for a in accounts)},
            )
        )
    elif accounts:
        account_id = next(iter(accounts))
    elif account_id is None and current_family is not None:
        account = await billing.account_of_family(session, current_family.id)
        account_id = None if account is None else account.id
    if account_id is None and current_family is None:
        blocking.append(PreviewBlocking(code="student_has_no_family"))
    credit = (
        Money.zero()
        if account_id is None
        else await billing.account_credit(session, account_id)
    )
    preview = _finish(
        {
            "student": student,
            "family": family,
            "billing_account_id": account_id,
            **extra,
        },
        subject=subject,
        lines=lines,
        automatic=automatic,
        automatic_allocations=automatic_allocations,
        account_credit=credit,
        blocking=blocking,
        warnings=warnings,
        status=status,
        version=version,
        at=at,
    )
    return _Computed(
        preview=preview,
        lines=lines,
        automatic=automatic,
        automatic_allocations=automatic_allocations,
        student_id=student_id,
        family_id=None if family is None else family.id,
        at=at,
        zone=zone,
        **computed,
    )


# --- Package subjects ----------------------------------------------------------------


async def _package_preview(
    session: AsyncSession, facts: billing.PackageFacts, *, now: datetime
) -> _Computed:
    subject = WithdrawalSubject(kind="package", id=facts.id)
    zone = await administration.get_center_time_zone(session)
    today = local_date(now, zone)
    existing = await billing.withdrawal_of_subject(session, "package", facts.id)
    blocking: list[PreviewBlocking] = []
    reason = facts.not_withdrawable(today)
    if reason is not None and existing is None:
        blocking.append(
            PreviewBlocking(code="package_not_withdrawable", details={"reason": reason})
        )
    charges = await billing.line_charges(
        session, [] if facts.purchase_charge_id is None else [facts.purchase_charge_id]
    )
    lines: list[_Line] = []
    if facts.purchase_charge_id is not None:
        charge = charges[facts.purchase_charge_id]
        lines.append(
            _line(
                charge,
                kind=charge.kind,
                service_date=charge.service_date,
                service_period_end=None,
                net=charge.net,
                consumed=facts.units_granted - max(0, facts.remaining),
                unused=max(0, facts.remaining),
                denominator=facts.units_granted,
            )
        )
    warnings: list[PreviewWarning] = []
    if reason is None:
        pending, future = await _package_sessions(session, facts, zone=zone, now=now)
        if pending:
            blocking.append(
                PreviewBlocking(
                    code="withdrawal_closeout_pending",
                    details={"class_session_ids": [str(i) for i in pending]},
                )
            )
        if future:
            warnings.append(
                PreviewWarning(
                    code="future_covered_sessions",
                    count=len(future),
                    class_session_ids=list(future),
                )
            )
    family = await families.family_record_reference(session, facts.family_id)
    return await _close(
        session,
        subject=subject,
        student_id=facts.student_id,
        lines=lines,
        automatic=[],
        automatic_allocations={},
        existing=existing,
        warnings=warnings,
        status=None,
        version=None,
        at=now,
        zone=zone,
        extra={
            "enrolled_at": None,
            "sessions": None,
            "units": PreviewUnits(
                unit=cast(Any, facts.unit),
                granted=facts.units_granted,
                consumed=facts.units_granted - max(0, facts.remaining),
                remaining=max(0, facts.remaining),
            ),
        },
        computed={"package": facts},
        blocking=blocking,
        account_id=facts.billing_account_id,
        family=None
        if family is None
        else PreviewRecord(id=family.id, name=family.name),
    )


async def _package_sessions(
    session: AsyncSession, facts: billing.PackageFacts, *, zone: str, now: datetime
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    """S7: the started sessions this package covers that await their
    closeout approval, and the future ones it covers (by start). Four or
    five statements."""
    participants = scheduling.billable_participant_columns(
        kinds=INDIVIDUAL,
        starts_from=floor_instant(facts.purchased_on, zone),
        student_id=facts.student_id,
    ).subquery("participants")
    rows = list(await session.execute(select(participants)))
    if not rows:
        return [], []
    keys = [(row.class_session_id, row.student_id) for row in rows]
    charged = await billing.session_charge_states(session, keys)
    rows = [r for r in rows if (r.class_session_id, r.student_id) not in charged]
    approved = await academics.approved_closeout_session_ids(
        session, {r.class_session_id for r in rows}
    )
    covered = await billing.keys_covered_by_package(
        session,
        facts.id,
        [
            billing.CoverageQuery(
                class_session_id=r.class_session_id,
                student_id=r.student_id,
                subject_id=r.subject_id,
                service_date=local_date(r.starts_at, zone),
                scheduled_minutes=r.scheduled_minutes,
                class_type=r.kind,
                closeout_approved=r.class_session_id in approved,
            )
            for r in rows
        ],
    )
    ordered = sorted(
        (r for r in rows if (r.class_session_id, r.student_id) in covered),
        key=lambda r: (r.starts_at, r.class_session_id),
    )
    pending = [r.class_session_id for r in ordered if r.starts_at <= now]
    future = [r.class_session_id for r in ordered if r.starts_at > now]
    return pending, future


# --- The preview (C1) ----------------------------------------------------------------


async def _compute(
    session: AsyncSession, kind: SubjectKind, subject_id: uuid.UUID, *, now: datetime
) -> _Computed:
    if kind == "package":
        facts = await billing.package_facts(session, subject_id)
        if facts is None:
            raise billing.WithdrawalSubjectNotFoundError()
        return await _package_preview(session, facts, now=now)
    state = await _enrollment_state(session, kind, subject_id)
    if state is None:
        raise billing.WithdrawalSubjectNotFoundError()
    return await _enrollment_preview(session, kind, state, now=now)


async def preview_withdrawal(
    session: AsyncSession, kind: SubjectKind, subject_id: uuid.UUID, *, now: datetime
) -> WithdrawalPreview:
    """``GET /api/v1/billing/withdrawal-preview`` (C1): read-only, no lock,
    no row written. Unknown subject → ``404 withdrawal_subject_not_found``."""
    return (await _compute(session, kind, subject_id, now=now)).preview


# --- The confirm (C2) ----------------------------------------------------------------


class _Stale(Exception):
    """A real effect differs from the prediction (rolls the savepoint back)."""


def _raise_blocking(preview: WithdrawalPreview) -> None:
    for block in preview.blocking:
        details = block.details
        if block.code == "withdrawal_exists":
            raise billing.WithdrawalExistsError(
                uuid.UUID(str(details["withdrawal_id"]))
            )
        if block.code == "package_not_withdrawable":
            raise billing.PackageNotWithdrawableError(str(details["reason"]))
        if block.code == "withdrawal_closeout_pending":
            ids = details.get("class_session_ids", [])
            raise billing.WithdrawalCloseoutPendingError(
                [uuid.UUID(i) for i in (ids if isinstance(ids, list) else [ids])]
            )
        if block.code == "student_has_no_family":
            raise billing.StudentHasNoFamilyAccountError()
        if block.code == "withdrawal_spans_accounts":
            ids = details.get("billing_account_ids", [])
            raise billing.WithdrawalSpansAccountsError(
                [uuid.UUID(i) for i in (ids if isinstance(ids, list) else [ids])]
            )


def _applied(computed: _Computed, overrides: Sequence[Any]) -> dict[int, Money]:
    """C2 step 6: the applied credit of each line (by index)."""
    applied = {i: line.item.computed_credit for i, line in enumerate(computed.lines)}
    by_charge = {
        line.item.charge_id: i
        for i, line in enumerate(computed.lines)
        if line.item.charge_id is not None
    }
    for position, override in enumerate(overrides):
        index = by_charge.get(override.charge_id)
        if index is None:
            raise billing.CreditOverrideUnknownChargeError(position)
        net = computed.lines[index].item.net
        if override.amount > net:
            raise billing.ChargeNetNegativeError(
                override.charge_id, net, -override.amount
            )
        applied[index] = override.amount
    return applied


async def _check_refund_terms(
    session: AsyncSession,
    terms: Any,
    freed: billing.FreedCash,
    computed: _Computed,
) -> None:
    if not await billing.active_payment_method(session, terms.payment_method_code):
        raise billing.PaymentMethodUnavailableError()
    if terms.refunded_on > await administration.center_today(session):
        raise billing.RefundedOnInFutureError()
    received = [
        a.received_on
        for line in computed.lines
        for a in line.allocations
        if a.payment_id in freed.by_payment
    ] + [
        a.received_on
        for rows in computed.automatic_allocations.values()
        for a in rows
        if a.payment_id in freed.by_payment
    ]
    if received and terms.refunded_on < max(received):
        raise billing.RefundedOnBeforePaymentError()


async def _lock_enrollment(
    uow: UnitOfWork, kind: SubjectKind, enrollment_id: uuid.UUID
) -> bool:
    if kind == "group_enrollment":
        locked = await scheduling.lock_group_enrollment_for_settlement(
            uow.session, enrollment_id
        )
        return locked is not None
    return await cohort_enrollment.lock_for_settlement(uow, enrollment_id) is not None


async def _withdraw(
    uow: UnitOfWork,
    computed: _Computed,
    *,
    reason: str,
    actor_id: uuid.UUID,
) -> None:
    """The M4 withdrawal of an active enrollment (the hook runs)."""
    period = computed.period
    assert period is not None and computed.version is not None  # noqa: S101
    if period.is_group:
        await group_enrollment.withdraw_student(
            uow.session,
            period.owner_id,
            period.enrollment_id,
            GroupWithdrawal(version=computed.version, reason=reason),
            actor_id=actor_id,
        )
        return
    await cohort_enrollment.withdraw_from_cohort(
        uow,
        period.owner_id,
        period.enrollment_id,
        CohortWithdrawal(version=computed.version, reason=reason),
        actor_id=actor_id,
    )


async def _post_due(session: AsyncSession, computed: _Computed) -> None:
    """S6: the "now" fee and tuition periods, under the account lock."""
    period = computed.period
    if period is None:
        return
    floor = await billing.billing_starts_on(session, lock=True)
    if floor is None:
        return
    if computed.now_fee is not None:
        fresh = (
            await enrollment_charges.group_period(session, period.enrollment_id)
            if period.is_group
            else await enrollment_charges.cohort_period(session, period.enrollment_id)
        )
        outcome = await enrollment_charges.classify_enrollment_period(
            session, fresh, zone=computed.zone, floor=floor
        )
        if outcome.outcome == "billable":
            await enrollment_charges.post_enrollment_fee(session, fresh, outcome)
    day = local_date(computed.at, computed.zone)
    for plan_id in computed.now_tuition_plans:
        await post_plan(session, plan_id, zone=computed.zone, today=day, first=floor)


async def _actual_lines(
    session: AsyncSession, computed: _Computed
) -> list[billing.LineCharge]:
    """The lines as posted now (under the account lock), in the preview's
    order; a line that isn't there, or differs, is :class:`_Stale`."""
    period = computed.period
    if period is None:
        ids = [c for line in computed.lines if (c := line.item.charge_id) is not None]
        found = await billing.line_charges(session, ids)
        return [found[i] for i in ids]
    fee_row = _fee_line_row(await _fee_rows(session, period), period)
    tuition: list[uuid.UUID] = []
    if period.is_group:
        plans = await billing.list_plans(
            session, group_enrollment_ids=[period.enrollment_id]
        )
        tuition = await billing.tuition_period_charge_ids(
            session,
            billing.plan_ids_of(plans),
            ends_from=local_date(computed.at, computed.zone),
        )
    ids = [*([fee_row.charge_id] if fee_row is not None else []), *tuition]
    charges = await billing.line_charges(session, ids)
    actual = [charges[i] for i in ids if not charges[i].withdrawal_credited]
    if len(actual) != len(computed.lines):
        raise _Stale()
    return actual


def _matches(line: _Line, actual: billing.LineCharge) -> bool:
    if line.item.charge_id is not None:
        return (
            actual.charge_id == line.item.charge_id
            and actual.net == line.item.net
            and {a.allocation_id for a in actual.allocations}
            == {a.allocation_id for a in line.allocations}
        )
    return (
        actual.kind == line.item.kind
        and actual.service_date == line.item.service_date
        and actual.net == line.item.net
        and not actual.allocations
    )


async def confirm_withdrawal(
    uow: UnitOfWork,
    principal: Principal,
    body: WithdrawalCreditConfirm | WithdrawalRefundConfirm,
    *,
    outcome: Outcome,
) -> WithdrawalConfirmed:
    """``POST /api/v1/billing/withdrawals/account-credit`` and ``…/refund``
    (C2; the module docstring's order). Never commits."""
    session = uow.session
    actor_id = principal.user_id
    kind, subject_id = body.subject.subject()
    now = clock.now()
    if kind == "package":
        facts = await billing.package_facts(session, subject_id)
        if facts is None:
            raise billing.WithdrawalSubjectNotFoundError()
        await billing.lock_billing_account(session, facts.billing_account_id)
        facts = await billing.package_facts(session, subject_id)
        assert facts is not None  # noqa: S101 - packages are never deleted
        computed = await _package_preview(session, facts, now=now)
    else:
        if await _enrollment_state(session, kind, subject_id) is None:
            raise billing.WithdrawalSubjectNotFoundError()
        ensure_permission(principal, SCHEDULE_MANAGE)
        if not await _lock_enrollment(uow, kind, subject_id):
            raise billing.WithdrawalSubjectNotFoundError()
        state = await _enrollment_state(session, kind, subject_id)
        assert state is not None  # noqa: S101 - locked above
        computed = await _enrollment_preview(session, kind, state, now=now)
    preview = computed.preview
    _raise_blocking(preview)
    if computed.status == "active":
        if body.version is None:
            raise billing.BillingValidationError(
                [
                    FieldError(
                        path=["body", "version"],
                        code="missing",
                        message="Send the enrollment's version.",
                    )
                ]
            )
        if body.version != computed.version:
            raise VersionConflictError()
    if body.fingerprint != preview.fingerprint:
        raise billing.WithdrawalPreviewStaleError(preview.model_dump(mode="json"))
    applied = _applied(computed, body.credit_overrides)
    freed = _freed(
        computed.lines, applied, computed.automatic, computed.automatic_allocations
    )
    if outcome == "refund":
        assert isinstance(body, WithdrawalRefundConfirm)  # noqa: S101 - the route
        if not freed.total.is_positive():
            raise billing.WithdrawalNothingToRefundError()
        await _check_refund_terms(session, body.refund, freed, computed)
    try:
        async with session.begin_nested():
            result = await _apply(
                uow,
                computed,
                applied=applied,
                freed=freed,
                body=body,
                outcome=outcome,
                actor_id=actor_id,
            )
    except _Stale:
        fresh = await _compute(session, kind, subject_id, now=now)
        raise billing.WithdrawalPreviewStaleError(
            fresh.preview.model_dump(mode="json")
        ) from None
    withdrawal_id, refund_transaction_id, account_id = result
    detail = await get_withdrawal(session, withdrawal_id)
    family_id = detail.family_id
    return WithdrawalConfirmed(
        withdrawal=detail,
        package_refund_transaction_id=refund_transaction_id,
        account_credit_after=await billing.account_credit(session, account_id),
        account=await get_summary(session, principal, family_id),
    )


async def _apply(
    uow: UnitOfWork,
    computed: _Computed,
    *,
    applied: dict[int, Money],
    freed: billing.FreedCash,
    body: WithdrawalCreditConfirm | WithdrawalRefundConfirm,
    outcome: Outcome,
    actor_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID | None, uuid.UUID]:
    """C2 steps 3 (the change) and 8-11, in the caller's savepoint."""
    session = uow.session
    preview = computed.preview
    if computed.status == "active":
        await _withdraw(uow, computed, reason=body.reason, actor_id=actor_id)
    if computed.period is not None:
        await billing.billing_starts_on(session, lock=True)
    account_id = preview.billing_account_id
    if account_id is None:
        assert computed.family_id is not None  # noqa: S101 - blocking otherwise
        account_id = await billing.ensure_billing_account(session, computed.family_id)
    await billing.lock_billing_account(session, account_id)
    await _post_due(session, computed)
    actual = await _actual_lines(session, computed)
    for line, real in zip(computed.lines, actual, strict=True):
        if not _matches(line, real):
            raise _Stale()
    if computed.automatic:
        keys = {
            enrollment_charges.removal_key(
                c.charge_id, preview.subject.id, "withdrawn"
            ): c
            for c in computed.automatic
        }
        found = await billing.adjustment_ids_by_key(session, keys)
        if set(found) != set(keys):
            raise _Stale()
        automatic_ids = list(found.values())
    else:
        automatic_ids = []
    refund_amount = freed.total if outcome == "refund" else Money.zero()
    amount = Money.total(applied.values())
    record = preview.model_dump(mode="json")
    record["outcome"] = outcome
    record["overrides"] = [
        {"charge_id": str(o.charge_id), "amount": str(o.amount)}
        for o in body.credit_overrides
    ]
    record["applied"] = [
        {"charge_id": str(real.charge_id), "applied_credit": str(applied[i])}
        for i, real in enumerate(actual)
    ]
    record["automatic_credit_keys"] = sorted(
        enrollment_charges.removal_key(c.charge_id, preview.subject.id, "withdrawn")
        for c in computed.automatic
    )
    withdrawal_id = await billing.insert_withdrawal(
        session,
        billing.WithdrawalDraft(
            billing_account_id=account_id,
            student_id=computed.student_id,
            kind=preview.subject.kind,
            subject_id=preview.subject.id,
            outcome=outcome,
            amount=amount,
            refund_amount=refund_amount,
            withdrawn_at=computed.at,
            reason=body.reason,
            preview=record,
        ),
    )
    adjustment_ids: list[uuid.UUID] = []
    reinserted: list[uuid.UUID] = []
    for i, real in enumerate(actual):
        credit = applied[i]
        if not credit.is_positive():
            continue
        posted = await billing.post_adjustment(
            session,
            billing.AdjustmentDraft(
                charge_id=real.charge_id,
                kind="withdrawal",
                amount=-credit,
                reason=body.reason,
                source_key=billing.WITHDRAWAL_KEY.format(
                    charge_id=real.charge_id, withdrawal_id=withdrawal_id
                ),
            ),
        )
        adjustment_ids.append(posted.adjustment_id)
        if posted.reinserted_allocation_id is not None:
            reinserted.append(posted.reinserted_allocation_id)
    refund_transaction_id: uuid.UUID | None = None
    if computed.package is not None:
        refund_transaction_id, _ = await billing.record_package_refund(
            session, computed.package.id, withdrawal_id
        )
    real_freed = await billing.freed_by_adjustments(
        session, [*adjustment_ids, *automatic_ids], reinserted
    )
    if real_freed != dict(freed.by_payment):
        raise _Stale()
    if outcome == "refund":
        assert isinstance(body, WithdrawalRefundConfirm)  # noqa: S101 - the route
        for payment_id, money in freed.by_payment.items():
            await billing.refund_payment(
                session,
                billing.RefundDraft(
                    payment_id=payment_id,
                    amount=money,
                    refunded_on=body.refund.refunded_on,
                    payment_method_code=body.refund.payment_method_code,
                    reference=body.refund.reference,
                    reason=body.reason,
                    withdrawal_id=withdrawal_id,
                ),
            )
    return withdrawal_id, refund_transaction_id, account_id


# --- Reads (D3) ----------------------------------------------------------------------


def _subject_of(row: billing.WithdrawalRow) -> WithdrawalSubject:
    if row.group_enrollment_id is not None:
        return WithdrawalSubject(kind="group_enrollment", id=row.group_enrollment_id)
    if row.cohort_enrollment_id is not None:
        return WithdrawalSubject(kind="cohort_enrollment", id=row.cohort_enrollment_id)
    assert row.package_id is not None  # noqa: S101 - one subject (CHECK)
    return WithdrawalSubject(kind="package", id=row.package_id)


def _keys(row: billing.WithdrawalRow) -> tuple[list[str], list[str]]:
    applied = row.preview.get("applied", [])
    own = [
        billing.WITHDRAWAL_KEY.format(charge_id=a["charge_id"], withdrawal_id=row.id)
        for a in applied
        if Money(a["applied_credit"]).is_positive()
    ]
    automatic = list(row.preview.get("automatic_credit_keys", []))
    return own, automatic


def _item(row: billing.WithdrawalRow, ids: Mapping[str, uuid.UUID]) -> dict[str, Any]:
    own, automatic = _keys(row)
    return {
        "id": row.id,
        "subject": _subject_of(row),
        "student_id": row.student_id,
        "family_id": row.family_id,
        "billing_account_id": row.billing_account_id,
        "outcome": row.outcome,
        "amount": row.amount,
        "refund_amount": row.refund_amount,
        "withdrawn_at": row.withdrawn_at,
        "reason": row.reason,
        "confirmed_by_user_id": row.confirmed_by_user_id,
        "created_at": row.created_at,
        "adjustment_ids": [ids[k] for k in own if k in ids],
        "refund_ids": list(row.refund_ids),
        "automatic_credit_adjustment_ids": [ids[k] for k in automatic if k in ids],
    }


async def get_withdrawal(
    session: AsyncSession, withdrawal_id: uuid.UUID
) -> WithdrawalDetail:
    """``GET /api/v1/billing/withdrawals/{withdrawal_id}`` (D3): two
    statements. Unknown → ``404``."""
    row = await billing.get_withdrawal(session, withdrawal_id)
    if row is None:
        raise RecordNotFoundError()
    own, automatic = _keys(row)
    ids = await billing.adjustment_ids_by_key(session, [*own, *automatic])
    return WithdrawalDetail(**_item(row, ids), preview=row.preview)


async def list_withdrawals(
    session: AsyncSession,
    *,
    family_id: uuid.UUID | None,
    student_id: uuid.UUID | None,
    group_enrollment_id: uuid.UUID | None,
    cohort_enrollment_id: uuid.UUID | None,
    package_id: uuid.UUID | None,
    limit: int,
    offset: int,
) -> OffsetPage[WithdrawalItem]:
    """``GET /api/v1/billing/withdrawals`` (D3): newest first; at most three
    statements."""
    rows, total = await billing.list_withdrawals(
        session,
        family_id=family_id,
        student_id=student_id,
        group_enrollment_id=group_enrollment_id,
        cohort_enrollment_id=cohort_enrollment_id,
        package_id=package_id,
        limit=limit,
        offset=offset,
    )
    keys: list[str] = []
    for row in rows:
        own, automatic = _keys(row)
        keys.extend([*own, *automatic])
    ids = await billing.adjustment_ids_by_key(session, keys)
    return OffsetPage[WithdrawalItem](
        items=[WithdrawalItem(**_item(row, ids)) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
