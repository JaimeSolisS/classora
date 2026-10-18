"""Entry point of the ``worker`` container: ``python -m app.worker``.

Runs the Procrastinate worker on the direct connection (ADR-0003 (b)), with
the tasks from :func:`app.core.jobs.create_job_app`. Periodic tasks are
deferred by this process too (ISSUE-J2); their ``queueing_lock`` and
Procrastinate's periodic deduplication keep it to one job per tick when
several workers run.

Start-up and shutdown:

- Settings are validated first; an invalid value prints the short
  ``Configuration error`` message and exits with status 1.
- The worker never applies the Procrastinate schema (Alembic owns it). Until
  ``make migrate`` has created it, the process waits, checks again every
  :data:`SCHEMA_POLL_SECONDS` and logs a line naming ``make migrate`` every
  :data:`WAITING_LOG_SECONDS`, then starts without a restart.
- If the worker stops on its own (lost connection, schema dropped), the
  process logs it and goes back to waiting; it does not exit.
- ``SIGTERM`` or ``SIGINT`` stops the worker gracefully: running jobs get
  ``WORKER_SHUTDOWN_TIMEOUT_SECONDS`` to finish and are aborted after that.
  The process then exits with status 0.

``python -m app.worker --healthcheck`` is the container healthcheck: it exits 0
when PostgreSQL answers on the direct URL, whether or not the schema exists.
"""

import asyncio
import contextlib
import logging
import signal
import sys
from collections.abc import Sequence

from procrastinate import App

from app.core.config import ConfigurationError, Settings, load_settings
from app.core.jobs import (
    WORKER_APPLICATION_NAME,
    WORKER_CONCURRENCY,
    create_worker_job_app,
    worker_healthcheck_connector,
)
from app.core.logging import configure_logging

logger = logging.getLogger("app.worker")

SCHEMA_POLL_SECONDS = 2.0
WAITING_LOG_SECONDS = 10.0
HEALTHCHECK_TIMEOUT_SECONDS = 4.0


async def wait_for_schema(
    app: App,
    stop: asyncio.Event,
    *,
    poll_seconds: float = SCHEMA_POLL_SECONDS,
    log_seconds: float = WAITING_LOG_SECONDS,
) -> bool:
    """Wait until the Procrastinate schema exists. ``False`` if stopped first.

    Never creates or changes the schema.
    """
    loop = asyncio.get_running_loop()
    last_log: float | None = None
    while not stop.is_set():
        try:
            if await app.check_connection_async():
                return True
            message = (
                "Procrastinate schema not found: run `make migrate`. "
                "The worker starts once it exists."
            )
        except Exception as exc:
            # The error class only: driver messages can name hosts.
            message = (
                f"Database not reachable; the worker retries ({type(exc).__name__})."
            )
        now = loop.time()
        if last_log is None or now - last_log >= log_seconds:
            logger.warning(message, extra={"action": "wait_for_schema"})
            last_log = now
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), poll_seconds)
    return False


def worker_options(settings: Settings) -> dict[str, object]:
    """Keyword arguments for ``App.run_worker_async``."""
    return {
        "name": WORKER_APPLICATION_NAME,
        "concurrency": WORKER_CONCURRENCY,
        "shutdown_graceful_timeout": float(settings.worker_shutdown_timeout_seconds),
        # Signals are handled by run() for the whole process lifetime.
        "install_signal_handlers": False,
    }


async def run(settings: Settings, stop: asyncio.Event) -> int:
    """Wait for the schema, then work until ``stop`` is set. Returns the exit code.

    If the Procrastinate worker stops on its own (for example the database
    restarted or the schema was downgraded), the error is logged and the
    process goes back to waiting for the schema instead of exiting, so a
    database restart never leaves the ``worker`` container stopped.
    """
    app = create_worker_job_app(settings)
    async with app.open_async():
        while True:
            if not await wait_for_schema(app, stop):
                logger.info("Worker stopped", extra={"action": "stop"})
                return 0
            worker = asyncio.create_task(
                app.run_worker_async(**worker_options(settings))  # type: ignore[arg-type]
            )
            stopping = asyncio.create_task(stop.wait())
            await asyncio.wait({worker, stopping}, return_when=asyncio.FIRST_COMPLETED)
            stopping.cancel()
            if not worker.done():
                # Graceful: Procrastinate stops fetching, waits up to the
                # shutdown timeout for running jobs, aborts the rest.
                worker.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await worker
                return 0
            # The worker stopped on its own (for example a lost connection).
            error = worker.exception()
            logger.error(
                "Worker stopped unexpectedly; waiting for the database to restart it",
                extra={
                    "action": "worker_restart",
                    # The error class only: driver messages can name hosts.
                    "error": type(error).__name__ if error is not None else None,
                },
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), SCHEMA_POLL_SECONDS)


async def _main_async(settings: Settings) -> int:
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    return await run(settings, stop)


async def healthcheck(settings: Settings) -> bool:
    """``True`` when PostgreSQL answers on the direct URL (schema or not)."""
    app = App(connector=worker_healthcheck_connector(settings))
    try:
        async with asyncio.timeout(HEALTHCHECK_TIMEOUT_SECONDS):
            async with app.open_async():
                await app.check_connection_async()
    except Exception:
        return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (see ConfigurationError).
        print(exc, file=sys.stderr)
        return 1
    if args == ["--healthcheck"]:
        return 0 if asyncio.run(healthcheck(settings)) else 1
    if args:
        print("usage: python -m app.worker [--healthcheck]", file=sys.stderr)
        return 2
    configure_logging()
    return asyncio.run(_main_async(settings))


if __name__ == "__main__":
    raise SystemExit(main())
