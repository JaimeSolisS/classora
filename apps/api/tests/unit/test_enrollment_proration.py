"""Pure parts of the enrollment charges (#92 G1, A2, B3, D1): the proration
of a fee, its order with the discounts, the ``ChargeDraft`` proration rules,
the owner counts and the after-commit deferral of
``billing:post_enrollment_charges``."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.core import jobs
from app.core.money import Money
from app.modules.billing.discounts import DiscountSpec, apply_discount_chain
from app.modules.billing.schemas import ChargeDraft
from app.modules.scheduling.service import OwnerSchedule
from app.workflows import enrollment_charge_tasks


@pytest.mark.parametrize(
    ("fee", "remaining", "total", "expected"),
    [
        ("1200.00", 7, 10, "840.00"),
        ("1000.00", 3, 7, "428.57"),
        ("0.05", 1, 3, "0.02"),
        ("1000.00", 7, 7, "1000.00"),
        ("1000.00", 0, 7, "0.00"),
    ],
)
def test_the_fee_is_prorated_once_half_up(fee, remaining, total, expected):
    assert Money(fee).prorate(remaining, total) == Money(expected)


def _spec(kind: str, *, percent: str | None = None, fixed: str | None = None):
    return DiscountSpec(
        discount_id=uuid.uuid4(),
        kind=kind,  # type: ignore[arg-type]
        name=kind,
        percent=None if percent is None else Decimal(percent),
        fixed_amount=None if fixed is None else Money(fixed),
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )


def test_proration_comes_before_the_discounts():
    prorated = Money("1000.00").prorate(3, 7)
    [line] = apply_discount_chain(prorated, [_spec("sibling", percent="10")])

    assert prorated == Money("428.57")
    assert line.amount == Money("42.86")
    assert prorated - line.amount == Money("385.71")


def test_a_fixed_discount_shows_the_order_matters():
    prorated = Money("1000.00").prorate(3, 7)
    [line] = apply_discount_chain(prorated, [_spec("discount", fixed="100.00")])

    assert prorated - line.amount == Money("328.57")  # prorate, then discount
    assert (Money("1000.00") - Money("100.00")).prorate(3, 7) == Money("385.71")


def _draft(**overrides):
    values = {
        "student_id": uuid.uuid4(),
        "kind": "cohort_enrollment",
        "cohort_enrollment_id": uuid.uuid4(),
        "service_date": date(2031, 3, 1),
        "list_amount": Money("700.00"),
        "due_date_base": date(2031, 3, 2),
        "proration": (7, 10),
    }
    values.update(overrides)
    return ChargeDraft(**values)


def test_a_draft_accepts_an_enrollment_proration():
    assert _draft().proration == (7, 10)
    assert _draft(proration=(0, 1)).proration == (0, 1)
    assert _draft(proration=None).proration is None


@pytest.mark.parametrize("proration", [(11, 10), (0, 0), (-1, 3), (1, 40000)])
def test_a_draft_refuses_a_proration_out_of_range(proration):
    with pytest.raises(ValueError, match="proration"):
        _draft(proration=proration)


def test_a_draft_refuses_a_proration_on_another_kind():
    with pytest.raises(ValueError, match="enrollment fee only"):
        _draft(
            kind="session",
            cohort_enrollment_id=None,
            class_session_id=uuid.uuid4(),
            scheduled_minutes=60,
        )


def test_the_counts_of_an_owner():
    start = datetime(2031, 3, 3, 22, 0, tzinfo=UTC)
    starts = [start + timedelta(days=7 * n) for n in range(10)]
    finite = OwnerSchedule(starts=starts)

    late = finite.counts(starts[2] + timedelta(minutes=30))
    assert (late.total, late.remaining, late.first_start) == (10, 7, starts[0])
    assert finite.counts(start - timedelta(days=1)).remaining == 10
    assert finite.counts(starts[2]).remaining == 7  # a session starting now
    assert OwnerSchedule(starts=starts, open_ended=True).counts(start).total is None
    empty = OwnerSchedule().counts(start)
    assert (empty.total, empty.remaining, empty.first_start) == (0, 0, None)


def test_the_task_shares_its_name_and_lock_with_core_jobs():
    assert enrollment_charge_tasks.TASK_NAME == jobs.ENROLLMENT_CHARGES_TASK
    assert enrollment_charge_tasks.QUEUEING_LOCK == (
        jobs.ENROLLMENT_CHARGES_QUEUEING_LOCK
    )


class _FakeDeferrer:
    def __init__(self, error: Exception | None) -> None:
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
async def test_the_deferral_names_the_task_its_lock_and_a_timestamp():
    app = _FakeJobApp()

    await jobs.defer_enrollment_charges(app)  # type: ignore[arg-type]

    [(name, options)] = app.configured
    assert name == "billing:post_enrollment_charges"
    assert options == {
        "allow_unknown": False,
        "queueing_lock": "billing-post-enrollment-charges",
    }
    [call] = app.deferrer.calls
    assert set(call) == {"timestamp"}


@pytest.mark.anyio
async def test_a_failing_deferral_is_logged_and_swallowed(caplog):
    from procrastinate.exceptions import AlreadyEnqueued

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_enrollment_charges(_FakeJobApp(AlreadyEnqueued("q")))  # type: ignore[arg-type]
        assert caplog.records == []
        await jobs.defer_enrollment_charges(_FakeJobApp(OSError("x")))  # type: ignore[arg-type]

    [record] = caplog.records
    assert record.message == "Deferring the enrollment charges failed"
