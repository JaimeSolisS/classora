"""The shutdown-signal hook of the lifespan (#42, QA D1): the signal first
schedules the callback on the loop, then reaches the previous handler
(uvicorn's) unchanged; leaving restores it."""

import asyncio
import signal
from types import FrameType

import pytest

from app.core.shutdown import SHUTDOWN_SIGNALS, on_shutdown_signal


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def previous_handlers():
    """A recording handler in place of each shutdown signal's (restored)."""
    received: list[int] = []

    def record(sig: int, _frame: FrameType | None) -> None:
        received.append(sig)

    originals = {sig: signal.signal(sig, record) for sig in SHUTDOWN_SIGNALS}
    try:
        yield record, received
    finally:
        for sig, original in originals.items():
            signal.signal(sig, original)


@pytest.mark.anyio
@pytest.mark.parametrize("sig", SHUTDOWN_SIGNALS)
async def test_the_signal_runs_the_callback_then_the_previous_handler(
    previous_handlers, sig: int
) -> None:
    record, received = previous_handlers
    called: list[str] = []
    with on_shutdown_signal(lambda: called.append("closed")):
        installed = signal.getsignal(sig)
        assert installed is not record
        assert callable(installed)
        installed(sig, None)
        assert received == [sig]  # uvicorn's handler still runs at once
        await asyncio.sleep(0)
        assert called == ["closed"]  # on the loop, not in the handler
    assert signal.getsignal(sig) is record


@pytest.mark.anyio
async def test_off_the_main_thread_it_changes_nothing(previous_handlers) -> None:
    record, _received = previous_handlers

    def in_thread() -> object:
        async def run() -> object:
            with on_shutdown_signal(lambda: None):
                return signal.getsignal(signal.SIGTERM)

        return asyncio.run(run())

    assert await asyncio.to_thread(in_thread) is record
