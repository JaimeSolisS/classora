"""Open streams end cleanly when the API process gets SIGTERM (#42 C5, QA D1).

Starts a real ``uvicorn`` process with the image's command line (including
``--timeout-graceful-shutdown 5``), opens a live-notification stream over
HTTP and sends the process SIGTERM. The stream must **end** (the chunked
body is terminated, so any proxy passes the end on and the browser
reconnects) well before the graceful timeout would cancel it, and the
process must log no ``Exception in ASGI application`` traceback.
"""

import os
import signal
import subprocess
import time

import httpx

from tests.authz.csrf import TEST_APP_ORIGIN
from tests.factories.identity import TEST_USER_PASSWORD
from tests.integration.auth_support import create_user
from tests.integration.test_rate_limits_multiprocess import (
    API_ROOT,
    api_command,
    free_port,
    wait_until_live,
)

STREAM = "/api/v1/notifications/stream"
GRACEFUL_SECONDS = 5


def test_sigterm_ends_open_streams_cleanly_and_quietly(module_seeded_url: str) -> None:
    user = create_user(module_seeded_url, roles=("owner",))
    port = free_port()
    env = {
        **os.environ,
        "APP_ENV": "local",
        "APP_ORIGIN": TEST_APP_ORIGIN,
        "SESSION_COOKIE_SECURE": "false",
        "DATABASE_URL": module_seeded_url,
        "DATABASE_DIRECT_URL": module_seeded_url,
    }
    env.pop("TRUSTED_PROXIES", None)
    command = [
        *api_command(port),
        "--timeout-graceful-shutdown",
        str(GRACEFUL_SECONDS),
    ]
    process = subprocess.Popen(  # noqa: S603 - fixed argument list
        command, cwd=API_ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        wait_until_live(process, base_url)
        with httpx.Client(
            base_url=base_url, headers={"Origin": TEST_APP_ORIGIN}, timeout=10
        ) as client:
            login = client.post(
                "/api/v1/auth/login",
                json={"username": user.username, "password": TEST_USER_PASSWORD},
            )
            assert login.status_code == 200, login.text
            with client.stream("GET", STREAM) as stream:
                assert stream.status_code == 200
                lines = stream.iter_lines()
                assert next(lines) == "retry: 5000"
                started = time.monotonic()
                process.send_signal(signal.SIGTERM)
                # A clean end: iteration stops without a protocol error.
                rest = list(lines)
                ended_after = time.monotonic() - started
        assert all(not line.startswith("event:") for line in rest)
        assert ended_after < GRACEFUL_SECONDS - 1
        # uvicorn re-raises the signal after its clean shutdown.
        assert process.wait(timeout=GRACEFUL_SECONDS + 5) in (0, -signal.SIGTERM)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    output = process.stdout.read().decode() if process.stdout else ""
    assert "Exception in ASGI application" not in output
    assert "Traceback" not in output
    assert "timeout graceful shutdown exceeded" not in output
