"""Operation kinds of the billing invariant sequences (#103 D1, D5, D6; U7).

Each builder takes the sequence's ``random.Random``, the context and the
current :class:`State` and returns an :class:`~.harness.Op`: its stable
label (targets by label, amounts) and an executor that runs the **real
workflow entry point or job** in its own Unit of Work and returns the
declared net-balance delta of the accepted operation (charge +amount,
adjustment +amount, payment −amount, allocation 0, reversal +payment
amount, refund +amount, due-date change and reminders 0, a job Σ the
charges and adjustments it wrote, matched against the counts it reports).

**Boundary bias (D5):** amounts come from
:func:`~.harness.boundary_amount` (exactly open / net / remainder / credit,
those + 0.01, and 0.01) and about one target in seven is an invalid one (a
reversed or refunded payment, a settled charge, another family's charge or
payment), so every kind reaches its refusal path.

This module holds the shared state reads and the ledger (D1) kinds; the
package and schedule kinds are in ``operations_packages`` and
``operations_schedule``.
"""

import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.errors import AppError
from app.core.money import Money
from app.modules.billing import schemas as bschemas
from app.workflows import (
    billing_accounts,
    billing_corrections,
    billing_payments,
    charge_reminders,
    session_charges,
)
from tests.factories.scheduling import add_participant, persist_class_session
from tests.integration.billing_invariants import checks
from tests.integration.billing_invariants.harness import (
    Ctx,
    Op,
    Outcome,
    accepted,
    boundary_amount,
    coin,
)
from tests.integration.billing_invariants.world import FamilyInfo, local

INVALID_TARGET_ODDS = 0.15
NOT_FOUND = "not_found"
CENT = Money("0.01")


# --- State ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChargeView:
    id: uuid.UUID
    label: str
    account: uuid.UUID
    student: uuid.UUID
    kind: str
    net: Money
    allocated: Money
    package: uuid.UUID | None

    @property
    def open(self) -> Money:
        return self.net - self.allocated


@dataclass(frozen=True)
class PaymentView:
    id: uuid.UUID
    label: str
    account: uuid.UUID
    amount: Money
    remainder: Money
    reversed: bool
    refunded: bool


@dataclass(frozen=True)
class PackageView:
    id: uuid.UUID
    label: str
    account: uuid.UUID
    student: uuid.UUID
    remaining: int
    expires_on: date
    closed: bool
    """Expired, cancelled or withdrawn."""


@dataclass(frozen=True)
class State:
    charges: list[ChargeView]
    payments: list[PaymentView]
    packages: list[PackageView]
    credit: dict[uuid.UUID, Money]

    def charges_of(self, account: uuid.UUID) -> list[ChargeView]:
        return [c for c in self.charges if c.account == account]

    def payments_of(self, account: uuid.UUID) -> list[PaymentView]:
        return [p for p in self.payments if p.account == account]

    def packages_of(self, account: uuid.UUID) -> list[PackageView]:
        return [p for p in self.packages if p.account == account]


_STATE_CHARGES = f"""
SELECT c.id, c.billing_account_id AS account, c.student_id AS student, c.kind,
       c.class_session_id, c.group_enrollment_id, c.cohort_enrollment_id,
       c.tuition_plan_id, c.package_id, c.service_date,
       {checks.NET} AS net, {checks.VALID} AS allocated
FROM charges c
"""  # noqa: S608 - constant SQL
_STATE_PAYMENTS = f"""
SELECT p.id, p.billing_account_id AS account, p.amount,
       p.amount - {checks.PAY_VALID} - {checks.PAY_REFUNDED} AS remainder,
       {checks.REVERSED} AS reversed,
       EXISTS (SELECT 1 FROM refunds f WHERE f.payment_id = p.id) AS refunded
FROM payments p
"""  # noqa: S608 - constant SQL
_STATE_PACKAGES = f"""
SELECT k.id, k.billing_account_id AS account, k.student_id AS student,
       k.expires_on, {checks.REMAINING} AS remaining,
       EXISTS (SELECT 1 FROM package_credit_transactions t WHERE t.package_id = k.id
               AND t.kind IN ('expire', 'reversal', 'refund')) AS closed
FROM packages k
"""  # noqa: S608 - constant SQL


def charge_label(ctx: Ctx, row: Any) -> str:
    """A charge's stable label: its own (manual), else from its source."""
    known = ctx.labels.get(row.id)
    if known is not None:
        return known
    if row.kind == "session":
        label = f"chg[{ctx.label(row.class_session_id)}/{ctx.label(row.student)}]"
    elif row.kind in ("group_enrollment", "cohort_enrollment"):
        source = row.group_enrollment_id or row.cohort_enrollment_id
        label = f"fee[{ctx.label(source)}@{row.service_date}]"
    elif row.kind == "tuition_period":
        label = f"tui[{ctx.label(row.tuition_plan_id)}@{row.service_date}]"
    elif row.kind == "package_purchase":
        label = f"buy[{ctx.label(row.package_id)}]"
    else:
        label = f"manual?{str(row.id)[-6:]}"
    ctx.labels[row.id] = label
    return label


async def read_state(ctx: Ctx) -> State:
    charges = sorted(
        (
            ChargeView(
                id=row.id,
                label=charge_label(ctx, row),
                account=row.account,
                student=row.student,
                kind=row.kind,
                net=Money(row.net),
                allocated=Money(row.allocated),
                package=row.package_id,
            )
            for row in await ctx.rows(_STATE_CHARGES)
        ),
        key=lambda c: c.label,
    )
    payments = sorted(
        (
            PaymentView(
                id=row.id,
                label=ctx.label(row.id),
                account=row.account,
                amount=Money(row.amount),
                remainder=Money(row.remainder),
                reversed=row.reversed,
                refunded=row.refunded,
            )
            for row in await ctx.rows(_STATE_PAYMENTS)
        ),
        key=lambda p: p.label,
    )
    packages = sorted(
        (
            PackageView(
                id=row.id,
                label=ctx.label(row.id),
                account=row.account,
                student=row.student,
                remaining=int(row.remaining),
                expires_on=row.expires_on,
                closed=row.closed,
            )
            for row in await ctx.rows(_STATE_PACKAGES)
        ),
        key=lambda p: p.label,
    )
    credit: dict[uuid.UUID, Money] = {}
    for p in payments:
        if not p.reversed:
            credit[p.account] = credit.get(p.account, Money.zero()) + p.remainder
    return State(charges, payments, packages, credit)


def family(rng: random.Random, ctx: Ctx) -> FamilyInfo:
    return ctx.world.families[rng.choice(sorted(ctx.world.families))]


def other_family(rng: random.Random, ctx: Ctx, info: FamilyInfo) -> FamilyInfo:
    others = sorted(label for label in ctx.world.families if label != info.label)
    return ctx.world.families[rng.choice(others)]


def student_of(rng: random.Random, ctx: Ctx, info: FamilyInfo) -> tuple[str, uuid.UUID]:
    label = rng.choice(list(info.students))
    return label, ctx.world.students[label]


def pick[T](rng: random.Random, items: list[T]) -> T | None:
    return rng.choice(items) if items else None


def _money(value: Money) -> str:
    return str(value.amount)


# --- Jobs ----------------------------------------------------------------------------

_NEW_ROWS = """
SELECT 'charge' AS row, id, billing_account_id AS account, amount, created_at
  FROM charges
UNION ALL SELECT 'adjustment', id, billing_account_id, amount, created_at
  FROM charge_adjustments
UNION ALL SELECT 'payment', id, billing_account_id, -amount, created_at FROM payments
UNION ALL SELECT 'refund', id, billing_account_id, amount, created_at FROM refunds
UNION ALL SELECT 'reversal', x.id, x.billing_account_id, p.amount, x.created_at
  FROM payment_reversals x JOIN payments p ON p.id = x.payment_id
"""
"""Every ledger row with its effect on the net balance (the suite's own
reading of data-model §4.2)."""


async def ledger_ids(ctx: Ctx) -> set[uuid.UUID]:
    return {row.id for row in await ctx.rows(_NEW_ROWS)}


async def new_rows_since(ctx: Ctx, before: set[uuid.UUID]) -> list[Any]:
    return [row for row in await ctx.rows(_NEW_ROWS) if row.id not in before]


async def transaction_effect(
    ctx: Ctx, account: uuid.UUID, table: str, row_id: uuid.UUID
) -> Money:
    """Σ the effects of the ledger rows on ``account`` written in the same
    transaction as ``table``'s row ``row_id`` (same ``created_at``, the
    transaction's ``now()``): what one operation wrote, even while other
    operations of a burst write on the same account."""
    [marker] = await ctx.rows(
        f"SELECT created_at FROM {table} WHERE id = :id",  # noqa: S608
        id=row_id,
    )
    rows = await ctx.rows(
        f"SELECT amount FROM ({_NEW_ROWS}) r WHERE account = :a AND created_at = :t",  # noqa: S608
        a=account,
        t=marker.created_at,
    )
    return Money.total(Money(row.amount) for row in rows)


def deltas_of(rows: list[Any]) -> dict[uuid.UUID, Money]:
    out: dict[uuid.UUID, Money] = {}
    for row in rows:
        out[row.account] = out.get(row.account, Money.zero()) + Money(row.amount)
    return out


type Runner = Callable[[Ctx], Awaitable[Any]]


def job_op(
    kind: str,
    runner: Runner,
    *,
    posted: Callable[[Any], int] | None = None,
    extra: Callable[[Ctx, Any, list[Any]], Awaitable[list[str]]] | None = None,
) -> Op:
    """A real ``run_*`` job: its delta is Σ the charges and adjustments it
    wrote; ``posted`` (the count it reports) must equal the charges it
    wrote; a failed item is a finding."""

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        before = await ledger_ids(ctx)
        result = await runner(ctx)
        rows = await new_rows_since(ctx, before)
        outcome = Outcome(True, deltas=deltas_of(rows), note=_counts(result))
        if getattr(result, "failed", 0):
            outcome.problems.append(f"{kind}: {result.failed} failed item(s): {result}")
        if posted is not None:
            charges = sum(1 for row in rows if row.row == "charge")
            if posted(result) != charges:
                outcome.problems.append(
                    f"{kind}: reported {posted(result)} posted, wrote {charges} charges"
                )
        if extra is not None:
            outcome.problems.extend(await extra(ctx, result, rows))
        return outcome

    return Op(kind, "", _run)


def _counts(result: Any) -> str:
    fields = getattr(result, "__dataclass_fields__", {})
    values = {name: getattr(result, name) for name in fields}
    return " ".join(f"{k}={v}" for k, v in values.items() if v)


def run_sessions_op() -> Op:
    async def _runner(ctx: Ctx) -> Any:
        return await session_charges.run_post_session_charges(
            ctx.settings, now=ctx.clock.now()
        )

    return job_op("run_sessions", _runner, posted=lambda r: r.posted)


def run_reminders_op() -> Op:
    """The reminder job, with the #97 step check: no reminder row for a
    charge whose open amount was 0 just before the run."""
    settled: list[set[uuid.UUID]] = []

    async def _runner(ctx: Ctx) -> Any:
        rows = await ctx.rows(
            f"SELECT c.id FROM charges c WHERE {checks.NET} - {checks.VALID} = 0"  # noqa: S608 - constant SQL
        )
        settled.append({row.id for row in rows})
        settled.append(
            {row.id for row in await ctx.rows("SELECT id FROM charge_reminders")}
        )
        return await charge_reminders.run_send_charge_reminders(
            ctx.settings, now=ctx.clock.now()
        )

    async def _extra(ctx: Ctx, result: Any, rows: list[Any]) -> list[str]:
        zero, known = settled
        fresh = await ctx.rows(
            "SELECT id, charge_id, stage FROM charge_reminders"
            " WHERE NOT (id = ANY(:k))",
            k=list(known),
        )
        return [
            f"R3: reminder {row.stage} for {ctx.label(row.charge_id)} with open 0"
            for row in fresh
            if row.charge_id in zero
        ]

    return job_op("run_reminders", _runner, extra=_extra)


# --- Transport (D8) ------------------------------------------------------------------


class RouteRefusal(AppError):
    """A ``4xx`` Problem Details response of a route, as its code (the
    route-driven sequence checks it against the declared refusals)."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__()
        self.code = code  # type: ignore[misc]
        self.status = status  # type: ignore[misc]


def account_path(family_id: object, suffix: str = "") -> str:
    return f"/api/v1/families/{family_id}/billing-account{suffix}"


async def via(
    ctx: Ctx,
    engine: AsyncEngine,
    role: str,
    path: str,
    body: BaseModel,
    workflow: Callable[[Any], Awaitable[BaseModel]],
) -> dict[str, Any]:
    """The workflow entry point in its own Unit of Work, or (when the
    context drives the routes, D8) the ``POST`` as ``role`` with a fresh
    ``Idempotency-Key``. Returns the response body as JSON."""
    if ctx.http is not None:
        status, payload = await ctx.http(role, path, body.model_dump(mode="json"))
        if 400 <= status < 500:
            raise RouteRefusal(str(payload.get("code")), status)
        if status not in (200, 201):
            raise AssertionError(f"{path}: HTTP {status} {payload}")
        found: dict[str, Any] = payload
        return found
    async with ctx.uow(engine) as uow:
        result = await workflow(uow)
    return result.model_dump(mode="json")


# --- Ledger operations (D1) ----------------------------------------------------------


def confirm_session(
    rng: random.Random, ctx: Ctx, state: State, *, subject: str = "IND"
) -> Op:
    """A new confirmed (or completed) individual session of a random
    student (one in twelve the unbilled ``X1``), for the session sweeper."""
    if coin(rng, 1 / 12):
        student_label = "X1"
    else:
        student_label, _ = student_of(rng, ctx, family(rng, ctx))
    student = ctx.world.students[student_label]
    day = ctx.today + timedelta(days=rng.randrange(-10, 11))
    minutes = rng.choice([60, 60, 45, 90])
    hour = rng.randrange(7, 20)
    state_name = rng.choice(["confirmed", "completed"])
    subject_id = ctx.world.subjects[subject]
    label_box: list[str] = []

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            made = await persist_class_session(
                uow.session,
                subject_id=subject_id,
                kind="individual",
                state=state_name,
                starts_at=local(day, hour),
                ends_at=local(day, hour) + timedelta(minutes=minutes),
            )
            await add_participant(uow.session, made, student)
        label_box.append(ctx.new_label("s", made.id))
        return Outcome(True, note=label_box[0])

    return Op(
        "confirm_session",
        f"{subject}/{student_label} {day} {minutes}min {state_name}",
        _run,
    )


def manual_charge(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op:
    info = info or family(rng, ctx)
    student_label, student = student_of(rng, ctx, info)
    invalid = coin(rng, INVALID_TARGET_ODDS)
    if invalid:
        student_label, student = student_of(rng, ctx, other_family(rng, ctx, info))
    amount, _ = boundary_amount(
        rng, [Money("250.00")], low=Money("0.01"), high=Money("600.00")
    )
    service = ctx.today + timedelta(days=rng.randrange(-20, 15))
    due = service + timedelta(days=rng.randrange(0, 20))
    body = bschemas.ManualChargeCreate.model_validate(
        {
            "student_id": str(student),
            "description": "Material de clase",
            "amount": _money(amount),
            "service_date": service.isoformat(),
            "due_date": due.isoformat(),
        }
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        posted = await via(
            ctx,
            engine,
            "finance",
            account_path(info.id, "/charges"),
            body,
            lambda uow: billing_corrections.post_manual_charge(
                uow.session, info.id, body
            ),
        )
        charge_id = uuid.UUID(str(posted["charge"]["id"]))
        ctx.new_label("M", charge_id)
        return accepted(info.account, amount, ctx.label(charge_id))

    return Op(
        "manual_charge",
        f"{info.label}/{student_label} {amount} due {due}",
        _run,
        account=info.account,
        refusals=frozenset({"student_not_in_family"}),
    )


def _charge_target(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo
) -> tuple[ChargeView | None, bool]:
    """A charge of ``info`` (an open one twice in three), or another
    family's (invalid)."""
    if coin(rng, INVALID_TARGET_ODDS):
        other = other_family(rng, ctx, info)
        return pick(rng, state.charges_of(other.account)), True
    own = state.charges_of(info.account)
    open_ = [c for c in own if c.open.is_positive()]
    if open_ and coin(rng, 2 / 3):
        return rng.choice(open_), False
    return pick(rng, own), False


def manual_adjustment(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or family(rng, ctx)
    charge, _ = _charge_target(rng, ctx, state, info)
    if charge is None:
        return None
    if coin(rng, 0.6):
        amount, _ = boundary_amount(
            rng,
            [charge.net, charge.open] if charge.net.is_positive() else [],
            low=CENT,
            high=max(charge.net, CENT),
        )
        amount = -amount
    else:
        amount, _ = boundary_amount(rng, [], low=CENT, high=Money("300.00"))
    body = bschemas.ManualAdjustmentCreate.model_validate(
        {"amount": _money(amount), "reason": "Corrección de captura"}
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        await via(
            ctx,
            engine,
            "finance",
            account_path(info.id, f"/charges/{charge.id}/adjustments"),
            body,
            lambda uow: billing_corrections.post_manual_adjustment(
                uow.session, info.id, charge.id, body
            ),
        )
        return accepted(info.account, amount)

    return Op(
        "manual_adjustment",
        f"{info.label} {charge.label} {amount}",
        _run,
        account=info.account,
        refusals=frozenset(
            {"charge_net_negative", "package_purchase_not_adjustable", NOT_FOUND}
        ),
    )


def _lines(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo
) -> list[tuple[ChargeView, Money]]:
    """1–2 explicit lines on the family's open charges (or another family's
    charge), amounts at the open boundary or below it."""
    lines: list[tuple[ChargeView, Money]] = []
    used: set[uuid.UUID] = set()
    for _ in range(rng.choice([1, 1, 2])):
        charge, _ = _charge_target(rng, ctx, state, info)
        if charge is None or charge.id in used:
            continue
        used.add(charge.id)
        amount, _ = boundary_amount(
            rng,
            [charge.open] if charge.open.is_positive() else [],
            low=CENT,
            high=max(charge.open, CENT),
        )
        lines.append((charge, amount))
    return lines


def record_payment(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op:
    info = info or family(rng, ctx)
    mode = rng.choice(["oldest_first", "oldest_first", "explicit", "none"])
    lines = _lines(rng, ctx, state, info) if mode == "explicit" else []
    if mode == "explicit" and not lines:
        mode = "oldest_first"
    outstanding = Money.total(c.open for c in state.charges_of(info.account))
    lines_total = Money.total(amount for _, amount in lines)
    if lines:
        amount = lines_total + (
            Money.zero()
            if coin(rng, 0.5)
            else boundary_amount(rng, [], low=CENT, high=Money("200.00"))[0]
        )
    else:
        amount, _ = boundary_amount(
            rng,
            [outstanding] if outstanding.is_positive() else [],
            low=CENT,
            high=Money("800.00"),
        )
    allocation: dict[str, Any] = {"mode": mode}
    if lines:
        allocation["lines"] = [
            {"charge_id": str(c.id), "amount": _money(a)} for c, a in lines
        ]
    body = bschemas.PaymentCreate.model_validate(
        {
            "amount": _money(amount),
            "received_on": ctx.today.isoformat(),
            "payment_method_code": rng.choice(["cash", "transfer"]),
            "allocation": allocation,
        }
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        recorded = await via(
            ctx,
            engine,
            "finance",
            account_path(info.id, "/payments"),
            body,
            lambda uow: billing_payments.record_payment(uow.session, info.id, body),
        )
        label = ctx.new_label("P", uuid.UUID(str(recorded["payment"]["id"])))
        return accepted(info.account, -amount, label)

    described = " ".join(f"{c.label}={a}" for c, a in lines)
    return Op(
        "record_payment",
        f"{info.label} {amount} {mode} {described}".strip(),
        _run,
        account=info.account,
        refusals=frozenset({"allocation_exceeds_open", "allocation_charge_not_found"}),
    )


def allocate_credit(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op:
    info = info or family(rng, ctx)
    credit = state.credit.get(info.account, Money.zero())
    mode = rng.choice(["oldest_first", "oldest_first", "explicit"])
    payload: dict[str, Any] = {"allocation": {"mode": mode}}
    described = mode
    if mode == "explicit":
        lines = _lines(rng, ctx, state, info)
        if not lines:
            payload = {"allocation": {"mode": "oldest_first"}}
        else:
            payload["allocation"]["lines"] = [
                {"charge_id": str(c.id), "amount": _money(a)} for c, a in lines
            ]
            described += " " + " ".join(f"{c.label}={a}" for c, a in lines)
    elif coin(rng, 0.6):
        amount, _ = boundary_amount(
            rng,
            [credit] if credit.is_positive() else [],
            low=CENT,
            high=Money("300.00"),
        )
        payload["amount"] = _money(amount)
        described += f" {amount}"
    body = bschemas.CreditAllocationCreate.model_validate(payload)

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        await via(
            ctx,
            engine,
            "finance",
            account_path(info.id, "/allocations"),
            body,
            lambda uow: billing_payments.allocate_credit(uow.session, info.id, body),
        )
        return accepted(info.account, Money.zero())

    return Op(
        "allocate_credit",
        f"{info.label} {described}",
        _run,
        account=info.account,
        refusals=frozenset(
            {
                "insufficient_account_credit",
                "nothing_to_allocate",
                "allocation_exceeds_open",
                "allocation_charge_not_found",
            }
        ),
    )


def _payment_target(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo
) -> PaymentView | None:
    if coin(rng, INVALID_TARGET_ODDS):
        return pick(rng, state.payments_of(other_family(rng, ctx, info).account))
    own = state.payments_of(info.account)
    usable = [p for p in own if not p.reversed]
    if usable and coin(rng, 0.75):
        return rng.choice(usable)
    return pick(rng, own)


def reverse_payment(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or family(rng, ctx)
    payment = _payment_target(rng, ctx, state, info)
    if payment is None:
        return None
    body = bschemas.PaymentReversalCreate.model_validate(
        {"reason": "Transferencia rechazada"}
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        await via(
            ctx,
            engine,
            "owner",
            account_path(info.id, f"/payments/{payment.id}/reversal"),
            body,
            lambda uow: billing_corrections.reverse_payment(
                uow.session, info.id, payment.id, body
            ),
        )
        return accepted(info.account, payment.amount)

    return Op(
        "reverse_payment",
        f"{info.label} {payment.label}",
        _run,
        account=info.account,
        refusals=frozenset(
            {"payment_already_reversed", "payment_has_refunds", NOT_FOUND}
        ),
    )


def refund_payment(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or family(rng, ctx)
    payment = _payment_target(rng, ctx, state, info)
    if payment is None:
        return None
    amount, _ = boundary_amount(
        rng,
        [payment.remainder] if payment.remainder.is_positive() else [],
        low=CENT,
        high=max(payment.remainder, CENT),
    )
    body = bschemas.RefundCreate.model_validate(
        {
            "amount": _money(amount),
            "refunded_on": ctx.today.isoformat(),
            "payment_method_code": "transfer",
            "reference": "SPEI-103",
            "reason": "Saldo a favor devuelto",
        }
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        await via(
            ctx,
            engine,
            "owner",
            account_path(info.id, f"/payments/{payment.id}/refunds"),
            body,
            lambda uow: billing_corrections.refund_payment(
                uow.session, info.id, payment.id, body
            ),
        )
        return accepted(info.account, amount)

    return Op(
        "refund_payment",
        f"{info.label} {payment.label} {amount}",
        _run,
        account=info.account,
        refusals=frozenset({"payment_reversed", "refund_exceeds_remainder", NOT_FOUND}),
    )


def due_date_change(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or family(rng, ctx)
    charge, _ = _charge_target(rng, ctx, state, info)
    if charge is None:
        return None
    new_due = ctx.today + timedelta(days=rng.randrange(-5, 25))
    body = bschemas.DueDateChangeCreate.model_validate(
        {"due_date": new_due.isoformat(), "reason": "Acuerdo con la familia"}
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        await via(
            ctx,
            engine,
            "owner",
            account_path(info.id, f"/charges/{charge.id}/due-date-changes"),
            body,
            lambda uow: billing_accounts.record_due_date_change(
                uow.session, info.id, charge.id, body
            ),
        )
        return accepted(info.account, Money.zero())

    return Op(
        "due_date_change",
        f"{info.label} {charge.label} → {new_due}",
        _run,
        account=info.account,
        refusals=frozenset({"charge_settled", "due_date_unchanged", NOT_FOUND}),
    )


def toggle_billing(rng: random.Random, ctx: Ctx, state: State) -> Op:
    """Billing off (``billing_starts_on`` NULL) or back on from the floor
    (raw SQL, as a fixture may): automatic charges stop, manual operations
    keep working."""
    from tests.integration.billing_invariants.world import FLOOR

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            current = await uow.session.scalar(
                text("SELECT billing_starts_on FROM billing_settings")
            )
            await uow.session.execute(
                text(
                    "UPDATE billing_settings SET billing_starts_on = :d,"
                    " version = version + 1"
                ),
                {"d": None if current is not None else FLOOR},
            )
        return Outcome(True, note="off" if current is not None else "on")

    return Op("toggle_billing", "", _run)


LEDGER_KINDS: dict[str, tuple[int, Callable[..., Op | None]]] = {
    "confirm_session": (9, confirm_session),
    "manual_charge": (8, manual_charge),
    "manual_adjustment": (8, manual_adjustment),
    "record_payment": (11, record_payment),
    "allocate_credit": (6, allocate_credit),
    "reverse_payment": (4, reverse_payment),
    "refund_payment": (5, refund_payment),
    "due_date_change": (4, due_date_change),
}
"""Ledger kinds with their weights (jobs and the clock are added by the
generator)."""

MONEY_KINDS = (
    "manual_charge",
    "manual_adjustment",
    "record_payment",
    "allocate_credit",
    "reverse_payment",
    "refund_payment",
)
"""The kinds a burst draws from (all on one account)."""


def weighted(rng: random.Random, table: dict[str, int]) -> str:
    names = sorted(table)
    return rng.choices(names, weights=[table[n] for n in names])[0]


def for_family(
    rng: random.Random,
    ctx: Ctx,
    state: State,
    builder: Callable[..., Op | None],
    info: FamilyInfo,
) -> Op | None:
    """``builder`` on the account of ``info`` (bursts)."""
    return builder(rng, ctx, state, info)
