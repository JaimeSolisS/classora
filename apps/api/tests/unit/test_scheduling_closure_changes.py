"""Pure rules of the series closure changes (#232 C1, C2, D3)."""

import uuid

import pytest

from app.core import jobs
from app.modules.administration import service as administration
from app.modules.scheduling import closure_changes, tasks
from app.modules.scheduling.states import SessionState


@pytest.mark.parametrize(
    ("previous", "audience"),
    [
        (SessionState.UNASSIGNED, set()),
        (SessionState.OFFERED, {"tutor"}),
        (SessionState.CONFIRMED, {"tutor", "family"}),
    ],
)
def test_who_is_told_follows_the_state_before_the_skip(previous, audience):
    assert closure_changes.skip_audience(previous.value) == audience


@pytest.mark.parametrize(
    "state",
    [
        SessionState.COMPLETED,
        SessionState.CANCELLED,
        SessionState.CONFLICT,
        SessionState.SKIPPED_CLOSURE,
    ],
)
def test_states_that_are_never_skipped_tell_nobody(state):
    assert state not in closure_changes.SKIPPABLE_STATES
    assert closure_changes.skip_audience(state.value) == frozenset()


def test_only_unassigned_offered_and_confirmed_rows_are_skipped():
    assert closure_changes.SKIPPABLE_STATES == {
        SessionState.UNASSIGNED,
        SessionState.OFFERED,
        SessionState.CONFIRMED,
    }


def test_the_notification_params_are_ids_and_an_instant_only():
    closure_changes.register_closure_notifications()
    closure_changes.register_closure_notifications()  # again: a no-op
    assert (
        closure_changes.OCCURRENCE_SKIPPED_CLOSURE
        in administration.registered_notification_types()
    )
    assert set(closure_changes.OccurrenceSkippedClosureParams.model_fields) == {
        "class_session_id",
        "series_id",
        "starts_at",
    }
    params = closure_changes.OccurrenceSkippedClosureParams(
        class_session_id=str(uuid.uuid4()),
        series_id=str(uuid.uuid4()),
        starts_at="2031-01-08T22:00:00Z",
    )
    assert (
        administration.validated_params(
            closure_changes.OCCURRENCE_SKIPPED_CLOSURE, params
        )
        == params.model_dump()
    )


def test_the_task_name_and_lock_are_shared_with_core_jobs():
    assert tasks.MATERIALIZE_TASK_NAME == jobs.MATERIALIZE_SERIES_TASK
    assert tasks.MATERIALIZE_QUEUEING_LOCK == jobs.MATERIALIZE_SERIES_QUEUEING_LOCK
    assert jobs.MATERIALIZE_SERIES_TASK == "scheduling:materialize_series"


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

    await jobs.defer_series_materialization(app)  # type: ignore[arg-type]

    [(name, options)] = app.configured
    assert name == jobs.MATERIALIZE_SERIES_TASK
    assert options == {
        "allow_unknown": False,
        "queueing_lock": jobs.MATERIALIZE_SERIES_QUEUEING_LOCK,
    }
    [call] = app.deferrer.calls
    assert set(call) == {"timestamp"} and isinstance(call["timestamp"], int)


@pytest.mark.anyio
async def test_an_already_queued_run_is_ignored_silently(caplog):
    from procrastinate.exceptions import AlreadyEnqueued

    app = _FakeJobApp(AlreadyEnqueued("queued"))

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_series_materialization(app)  # type: ignore[arg-type]

    assert caplog.records == []


@pytest.mark.anyio
async def test_any_other_failure_is_logged_and_swallowed(caplog):
    app = _FakeJobApp(OSError("unreachable"))

    with caplog.at_level("ERROR", logger=jobs.__name__):
        await jobs.defer_series_materialization(app)  # type: ignore[arg-type]

    [record] = caplog.records
    assert record.message == "Deferring the series materialization failed"
    assert record.error == "OSError"
    assert "unreachable" not in record.getMessage()
