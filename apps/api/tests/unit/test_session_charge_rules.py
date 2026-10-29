"""Pure parts of the session charges (#91): the local service date, the
floor's local midnight, the price key, the settings body and the after-commit
deferral of ``billing:post_session_charges`` (B4)."""

import uuid
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.core import jobs
from app.modules.billing.schemas import BillingSettingsUpdate
from app.workflows import session_charge_tasks, session_charges

MEXICO = "America/Mexico_City"


def test_the_service_date_is_the_center_local_date():
    late = datetime(2031, 3, 2, 5, 30, tzinfo=UTC)  # 23:30 on 1 March, UTC-6

    assert session_charges.local_date(late, MEXICO) == date(2031, 3, 1)
    assert session_charges.local_date(late, "Europe/Madrid") == date(2031, 3, 2)


def test_the_floor_starts_at_local_midnight():
    assert session_charges.floor_instant(date(2031, 3, 1), MEXICO) == datetime(
        2031, 3, 1, 6, 0, tzinfo=UTC
    )


def test_the_price_key_is_individual_per_session_on_the_service_date():
    candidate = session_charges.Candidate(
        class_session_id=uuid.uuid4(),
        student_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        starts_at=datetime(2031, 3, 2, 5, 30, tzinfo=UTC),
        ends_at=datetime(2031, 3, 2, 6, 15, tzinfo=UTC),
        state="confirmed",
        scheduled_minutes=45,
        service_date=date(2031, 3, 1),
    )
    level = uuid.uuid4()

    key = session_charges.price_key(candidate, level)

    assert key == session_charges.PriceKey(
        subject_id=candidate.subject_id,
        education_level_id=level,
        duration_minutes=45,
        on=date(2031, 3, 1),
        class_type="individual",
        pricing_model="per_session",
    )
    assert session_charges.ELIGIBLE_KINDS == ("individual", "group")


def test_the_settings_body_needs_both_keys_and_a_sane_date():
    assert BillingSettingsUpdate.model_validate(
        {"version": 3, "billing_starts_on": None}
    ) == BillingSettingsUpdate(version=3, billing_starts_on=None)
    for body in (
        {"version": 3},
        {"billing_starts_on": "2031-03-01"},
        {"version": 3, "billing_starts_on": "2031-03-01", "other": 1},
        {"version": 3, "billing_starts_on": "2100-01-01"},
    ):
        with pytest.raises(ValidationError):
            BillingSettingsUpdate.model_validate(body)


def test_the_task_shares_its_name_and_lock_with_core_jobs():
    assert session_charge_tasks.TASK_NAME == jobs.SESSION_CHARGES_TASK
    assert session_charge_tasks.QUEUEING_LOCK == jobs.SESSION_CHARGES_QUEUEING_LOCK


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

    await jobs.defer_session_charges(app)  # type: ignore[arg-type]

    [(name, options)] = app.configured
    assert name == "billing:post_session_charges"
    assert options == {
        "allow_unknown": False,
        "queueing_lock": "billing-post-session-charges",
    }
    [call] = app.deferrer.calls
    assert set(call) == {"timestamp"} and isinstance(call["timestamp"], int)


@pytest.mark.anyio
async def test_an_already_queued_run_is_ignored_silently(caplog):
    from procrastinate.exceptions import AlreadyEnqueued

    app = _FakeJobApp(AlreadyEnqueued("queued"))

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_session_charges(app)  # type: ignore[arg-type]

    assert caplog.records == []


@pytest.mark.anyio
async def test_any_other_failure_is_logged_and_swallowed(caplog):
    app = _FakeJobApp(OSError("unreachable"))

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_session_charges(app)  # type: ignore[arg-type]

    [record] = caplog.records
    assert record.message == "Deferring the session charges failed"
    assert record.error == "OSError"
