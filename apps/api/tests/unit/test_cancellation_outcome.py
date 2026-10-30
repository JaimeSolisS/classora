"""The pure rules of schedule reconciliation (#99 B2, D6, D9, E1): the
cancellation outcome for every classification × makeup × rule column, the
repricing target (original, catalog over a percentage-then-fixed snapshot,
pro rata rounding) and the clamp, and the after-commit deferral of the job
(also at the end of the series materialization)."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from app.core import jobs
from app.core.money import Money
from app.modules.billing import service as billing
from app.modules.billing.discounts import DiscountLine
from app.modules.scheduling import tasks as scheduling_tasks
from app.workflows.schedule_reconciliation import clamp_delta, repricing_target

CLASSIFICATIONS = ("excused", "on_time", "late", "no_show")


def rule(**outcomes: str) -> billing.CancellationRuleValues:
    values = {f"{c}_outcome": "credit" for c in CLASSIFICATIONS} | outcomes
    return billing.CancellationRuleValues(
        id=uuid.uuid4(),
        effective_from=datetime(2000, 1, 1, tzinfo=UTC),
        **values,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("classification", CLASSIFICATIONS)
@pytest.mark.parametrize("makeup", [True, False])
@pytest.mark.parametrize("column", ["credit", "charge"])
def test_the_outcome_of_every_combination(classification, makeup, column):
    values = rule(**{f"{classification}_outcome": column})
    # Every other column says the opposite, so the right one is read.
    others = {
        f"{c}_outcome": "charge" if column == "credit" else "credit"
        for c in CLASSIFICATIONS
        if c != classification
    }
    values = rule(**{f"{classification}_outcome": column, **others})

    outcome = billing.cancellation_outcome(values, classification, makeup)

    assert outcome == ("credit" if makeup else column)


def test_an_unknown_classification_is_a_value_error():
    with pytest.raises(ValueError, match="unknown classification"):
        billing.cancellation_outcome(rule(), "early", False)


def charge(
    amount: str = "250.00",
    *,
    minutes: int = 60,
    net: str | None = None,
    duration_total: str = "0.00",
) -> billing.ReconciliationCharge:
    return billing.ReconciliationCharge(
        charge_id=uuid.uuid4(),
        billing_account_id=uuid.uuid4(),
        family_id=uuid.uuid4(),
        student_id=uuid.uuid4(),
        amount=Money(amount),
        scheduled_minutes=minutes,
        service_date=date(2031, 3, 10),
        education_level_id=None,
        posted_at=datetime(2031, 3, 1, tzinfo=UTC),
        net=Money(net or amount),
        closure_total=Money.zero(),
        closure_count=0,
        duration_total=Money(duration_total),
        billed_minutes=minutes,
    )


def line(position: int, *, percent: str | None = None, fixed: str | None = None) -> Any:
    return DiscountLine(
        discount_id=uuid.uuid4(),
        position=position,
        kind="discount" if percent else "promotion",
        name=f"D{position}",
        percent=None if percent is None else Decimal(percent),
        fixed_amount=None if fixed is None else Money(fixed),
        amount=Money("1.00"),
    )


def test_the_charges_own_minutes_give_its_own_amount():
    target = repricing_target(charge("205.00"), 60, list_amount=Money("999.00"))

    assert (target.amount, target.method) == (Money("205.00"), "original")


def test_the_catalog_target_reapplies_the_snapshot_in_position_order():
    # Given out of order: 10 % first (position 1), then 20.00 fixed.
    snapshot = [line(2, fixed="20.00"), line(1, percent="10")]

    target = repricing_target(
        charge("205.00"), 45, list_amount=Money("200.00"), snapshot=snapshot
    )

    assert (target.amount, target.method) == (Money("160.00"), "catalog")


def test_without_a_price_the_amount_is_prorated_half_up():
    assert repricing_target(charge(), 50, list_amount=None).amount == Money("208.33")
    assert repricing_target(charge("0.10"), 45, list_amount=None).amount == Money(
        "0.08"
    )  # 0.075 → 0.08
    longer = repricing_target(charge(), 90, list_amount=None)
    assert (longer.amount, longer.method) == (Money("375.00"), "pro_rata")


def test_the_delta_is_from_the_billed_amount_and_clamped_at_zero():
    # A manual −30 doesn't count as billed: 200 − (250 + 0) = −50.
    assert clamp_delta(Money("200.00"), charge(net="220.00")) == (
        Money("-50.00"),
        False,
    )
    # An earlier duration change does: 150 − (250 − 50) = −50.
    assert clamp_delta(
        Money("150.00"), charge(net="200.00", duration_total="-50.00")
    ) == (Money("-50.00"), False)
    # Net 10 and −50: only −10.
    assert clamp_delta(Money("200.00"), charge(net="10.00")) == (Money("-10.00"), True)
    assert clamp_delta(Money("200.00"), charge(net="0.00")) == (Money.zero(), True)


# --- D9: the deferral ----------------------------------------------------------------


class _FakeDeferrer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, object]] = []

    async def defer_async(self, **kwargs: object) -> int:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return 1


class _FakeJobApp:
    def __init__(self, error: Exception | None = None) -> None:
        self.deferrer = _FakeDeferrer(error)
        self.configured: list[tuple[str, dict[str, object]]] = []

    def configure_task(self, name: str, **options: object) -> _FakeDeferrer:
        self.configured.append((name, options))
        return self.deferrer


@pytest.mark.anyio
async def test_the_deferral_names_the_task_and_its_lock():
    app = _FakeJobApp()

    await jobs.defer_schedule_reconciliation(app)  # type: ignore[arg-type]

    [(name, options)] = app.configured
    assert name == "billing:reconcile_session_charges"
    assert options == {
        "allow_unknown": False,
        "queueing_lock": "billing-reconcile-session-charges",
    }
    [call] = app.deferrer.calls
    assert set(call) == {"timestamp"} and isinstance(call["timestamp"], int)


@pytest.mark.anyio
async def test_a_queued_run_is_ignored_and_other_failures_are_logged(caplog):
    from procrastinate.exceptions import AlreadyEnqueued

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_schedule_reconciliation(_FakeJobApp(AlreadyEnqueued("q")))  # type: ignore[arg-type]
        assert caplog.records == []
        await jobs.defer_schedule_reconciliation(_FakeJobApp(OSError("down")))  # type: ignore[arg-type]

    [record] = caplog.records
    assert record.message == "Deferring the schedule reconciliation failed"
    assert record.error == "OSError"


@pytest.mark.anyio
async def test_the_materialization_queues_a_reconciliation_after_its_run(monkeypatch):
    order: list[str] = []

    async def run(settings: Any) -> None:
        del settings
        order.append("materialized")

    monkeypatch.setattr(scheduling_tasks, "run_materialization", run)
    monkeypatch.setattr(scheduling_tasks, "get_settings", lambda: None)
    app = _FakeJobApp()
    task = scheduling_tasks.materialization_task(app)  # type: ignore[arg-type]

    await task(timestamp=0)
    order.append("after")

    assert order == ["materialized", "after"]
    [(name, _)] = app.configured
    assert name == jobs.SCHEDULE_RECONCILIATION_TASK
