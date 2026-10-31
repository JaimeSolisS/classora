"""Package operation kinds (#103 D2): purchase, covered sessions, closeout
approval (consumption through ``on_closeout_approved``), the expiry job,
``cancel_package`` and package withdrawals (account credit and refund).

The purchase and the withdrawals declare the effect of the rows they wrote
(a purchase charge, withdrawal credits, refunds); a cancellation declares
minus its credit, as its result reports it.
"""

import random
import uuid
from datetime import UTC
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncEngine

from app.modules.billing import schemas as bschemas
from app.modules.identity.permissions import OWNER
from app.workflows import package_expiry, packages, withdrawals
from app.workflows.closeout_approval import on_closeout_approved
from app.workflows.package_schemas import PackagePurchase
from app.workflows.withdrawal_schemas import (
    SubjectKind,
    WithdrawalCreditConfirm,
    WithdrawalRefundConfirm,
)
from tests.integration import package_support, withdrawal_support
from tests.integration.billing_invariants import operations as ops
from tests.integration.billing_invariants.harness import (
    Ctx,
    Op,
    Outcome,
    accepted,
    boundary_amount,
    coin,
)
from tests.integration.billing_invariants.operations import CENT, NOT_FOUND, State
from tests.integration.billing_invariants.world import FamilyInfo

WITHDRAWAL_REFUSALS = frozenset(
    {
        "package_not_withdrawable",
        "withdrawal_exists",
        "withdrawal_nothing_to_refund",
        "withdrawal_closeout_pending",
        "withdrawal_preview_stale",
        "withdrawal_subject_not_found",
        "withdrawal_subject_invalid",
    }
)


def principal(ctx: Ctx) -> Any:
    return package_support._principal(ctx.world.owner.id, OWNER)


def purchase(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op:
    info = info or ops.family(rng, ctx)
    student_label, student = ops.student_of(rng, ctx, info)
    if coin(rng, 0.1):
        student_label, student = "X1", ctx.world.students["X1"]
    body = PackagePurchase(student_id=student, package_product_id=ctx.world.product)

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            detail = await packages.purchase_package(uow.session, body)
        ctx.new_label("K", detail.id)
        delta = await ops.transaction_effect(ctx, info.account, "packages", detail.id)
        return accepted(info.account, delta, ctx.label(detail.id))

    return Op(
        "purchase_package",
        f"{info.label}/{student_label}",
        _run,
        account=info.account,
        refusals=frozenset({"student_has_no_family"}),
    )


async def _pending_sessions(ctx: Ctx) -> list[Any]:
    """Our PKG sessions with no approval yet (by label)."""
    rows = await ctx.rows(
        "SELECT s.id FROM class_sessions s WHERE s.subject_id = :subject"
        " ORDER BY s.starts_at, s.id",
        subject=ctx.world.subjects["PKG"],
    )
    return sorted(
        (row for row in rows if ctx.labels.get(row.id) is not None),
        key=lambda row: ctx.label(row.id),
    )


def approve_closeout(
    rng: random.Random, ctx: Ctx, state: State, *, sessions: list[Any]
) -> Op | None:
    """``on_closeout_approved`` for one of our package-subject sessions
    (revision 1; approving it again finds it consumed or charged)."""
    target = ops.pick(rng, sessions)
    if target is None:
        return None
    session_id = target.id

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            await on_closeout_approved(
                uow.session,
                approval_id=uuid.uuid7(),
                supersedes_approval_id=None,
                class_session_id=session_id,
            )
        return Outcome(True)

    return Op("approve_closeout", ctx.label(session_id), _run)


def run_expiry_op() -> Op:
    async def _runner(ctx: Ctx) -> Any:
        return await package_expiry.run_expire_packages(
            ctx.settings, now=ctx.clock.now()
        )

    return ops.job_op("run_expiry", _runner)


def _package_target(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo
) -> ops.PackageView | None:
    if coin(rng, ops.INVALID_TARGET_ODDS):
        return ops.pick(
            rng, state.packages_of(ops.other_family(rng, ctx, info).account)
        )
    own = state.packages_of(info.account)
    usable = [p for p in own if not p.closed and p.remaining > 0]
    if usable and coin(rng, 0.7):
        return rng.choice(usable)
    return ops.pick(rng, own)


def cancel(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or ops.family(rng, ctx)
    package = _package_target(rng, ctx, state, info)
    if package is None:
        return None
    purchase_charge = next(
        (
            c
            for c in state.charges
            if c.package == package.id and c.kind == "package_purchase"
        ),
        None,
    )
    payload: dict[str, Any] = {"reason": "La familia se mudó"}
    if purchase_charge is not None and coin(rng, 0.5):
        amount, _ = boundary_amount(
            rng,
            [purchase_charge.net] if purchase_charge.net.is_positive() else [],
            low=CENT,
            high=max(purchase_charge.net, CENT),
        )
        payload["credit_amount"] = str(amount.amount)
    body = bschemas.PackageCancellationCreate.model_validate(payload)

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        async with ctx.uow(engine) as uow:
            result = await packages.cancel_package(uow.session, package.id, body)
        return accepted(package.account, -result.credit, f"credit={result.credit}")

    return Op(
        "cancel_package",
        f"{info.label} {package.label}"
        f" credit={payload.get('credit_amount', 'default')}",
        _run,
        account=package.account,
        refusals=frozenset(
            {"package_not_cancellable", "charge_net_negative", NOT_FOUND}
        ),
    )


def withdraw(
    rng: random.Random,
    ctx: Ctx,
    state: State,
    *,
    kind: str,
    subject: uuid.UUID,
    subject_label: str,
    account: uuid.UUID | None = None,
    refund: bool | None = None,
) -> Op:
    """A #101 withdrawal of ``subject`` (preview, then confirm with its
    fingerprint): account credit, or a refund of the refundable cash."""
    refund = coin(rng, 0.5) if refund is None else refund

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        from tests.integration.billing_invariants.operations_schedule import (
            effect_of_transaction,
            transaction_now,
        )

        async with ctx.uow(engine) as uow:
            at = await transaction_now(uow.session)
            found = await withdrawals.preview_withdrawal(
                uow.session,
                cast(SubjectKind, kind),
                subject,
                now=ctx.clock.now().astimezone(UTC),
            )
            payload = withdrawal_support.body(
                found.model_dump(mode="json"),
                refund_on=ctx.today if refund else None,
            )
            body: WithdrawalCreditConfirm = (
                WithdrawalRefundConfirm.model_validate(payload)
                if refund
                else WithdrawalCreditConfirm.model_validate(payload)
            )
            await withdrawals.confirm_withdrawal(
                uow,
                principal(ctx),
                body,
                outcome="refund" if refund else "account_credit",
            )
        return Outcome(True, deltas=await effect_of_transaction(ctx, at))

    return Op(
        "withdraw_refund" if refund else "withdraw_credit",
        f"{kind} {subject_label}",
        _run,
        refusals=WITHDRAWAL_REFUSALS
        | {"enrollment_not_active", "version_conflict", "charge_net_negative"},
    )


def withdraw_package(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    info = info or ops.family(rng, ctx)
    paid = {
        c.package
        for c in state.charges_of(info.account)
        if c.kind == "package_purchase" and c.allocated.is_positive()
    }
    usable = [
        p for p in state.packages_of(info.account) if p.id in paid and not p.closed
    ]
    refund = coin(rng, 0.5)
    package = (
        rng.choice(usable)
        if usable and (refund or coin(rng, 0.6))
        else _package_target(rng, ctx, state, info)
    )
    if package is None:
        return None
    return withdraw(
        rng,
        ctx,
        state,
        kind="package",
        subject=package.id,
        subject_label=package.label,
        account=package.account,
        refund=refund,
    )


def pay_package(
    rng: random.Random, ctx: Ctx, state: State, info: FamilyInfo | None = None
) -> Op | None:
    """A payment of exactly the open amount of one open purchase charge
    (explicit line), so withdrawals and cancellations find paid packages."""
    info = info or ops.family(rng, ctx)
    open_ = [
        c
        for c in state.charges_of(info.account)
        if c.kind == "package_purchase" and c.open.is_positive()
    ]
    target = ops.pick(rng, open_)
    if target is None:
        return None
    body = bschemas.PaymentCreate.model_validate(
        {
            "amount": str(target.open.amount),
            "received_on": ctx.today.isoformat(),
            "payment_method_code": "cash",
            "allocation": {
                "mode": "explicit",
                "lines": [
                    {"charge_id": str(target.id), "amount": str(target.open.amount)}
                ],
            },
        }
    )

    async def _run(ctx: Ctx, engine: AsyncEngine) -> Outcome:
        from app.workflows import billing_payments

        async with ctx.uow(engine) as uow:
            recorded = await billing_payments.record_payment(uow.session, info.id, body)
        return accepted(
            info.account, -target.open, ctx.new_label("P", recorded.payment.id)
        )

    return Op(
        "pay_package",
        f"{info.label} {target.label} {target.open}",
        _run,
        account=info.account,
        refusals=frozenset({"allocation_exceeds_open"}),
    )
