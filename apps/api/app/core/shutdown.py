"""Run a callback as soon as a shutdown signal arrives (#42, QA D1).

uvicorn handles ``SIGTERM``/``SIGINT`` by waiting for open responses to
finish and only then running the lifespan shutdown. Responses that never
finish on their own (the live-notification streams) are cancelled at the
graceful-shutdown timeout, which aborts them: a proxy in front may never
pass that on, and uvicorn logs a ``CancelledError`` traceback.

:func:`on_shutdown_signal` lets the lifespan act at the signal itself, before
uvicorn's wait: it wraps the current handlers (uvicorn installs its own
before the lifespan starts) so the signal first schedules ``callback`` on
the event loop and then reaches the previous handler unchanged. The
lifespan uses it to end every stream cleanly, so the wait is short.

Signals can only be handled in the main thread: elsewhere (an app inside
``TestClient``) it does nothing.
"""

import asyncio
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any, Final

SHUTDOWN_SIGNALS: Final = (signal.SIGTERM, signal.SIGINT)


@contextmanager
def on_shutdown_signal(callback: Callable[[], None]) -> Iterator[None]:
    """While active, a shutdown signal schedules ``callback`` on the running
    loop, then calls the handler that was installed before."""
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    loop = asyncio.get_running_loop()
    previous: dict[int, Any] = {
        int(sig): signal.getsignal(sig) for sig in SHUTDOWN_SIGNALS
    }

    def handler(sig: int, frame: FrameType | None) -> None:
        loop.call_soon_threadsafe(callback)
        before = previous[sig]
        if callable(before):
            before(sig, frame)
        else:
            # SIG_DFL or SIG_IGN: restore it and let it act.
            signal.signal(sig, before)
            if before == signal.SIG_DFL:
                signal.raise_signal(sig)

    for sig in SHUTDOWN_SIGNALS:
        signal.signal(sig, handler)
    try:
        yield
    finally:
        for number, before in previous.items():
            if signal.getsignal(number) is handler:
                signal.signal(number, before)
