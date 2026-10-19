"""Two API processes share the limits (#27 E; ADR-0008 D15).

Starts two real ``uvicorn`` processes, with the image's command line
(``--factory app.main:create_app --no-proxy-headers``), on two free ports,
both on the same migrated Testcontainers database, and alternates requests
between them. The counters live in PostgreSQL, so the limit is the same
whichever process answers; an in-process counter would let each process
allow its own limit.

These processes use the real clock (the ``clock`` fixture cannot reach
them). If a run crosses a window boundary, the counts split over two windows:
the test empties the counters and repeats once; a second crossing fails.
"""

import os
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from app.core.ratelimit import key_hash
from tests.authz.csrf import TEST_APP_ORIGIN
from tests.integration.conftest import reset_rate_limits, run_sql

API_ROOT = Path(__file__).resolve().parents[2]
LOGIN = "/api/v1/auth/login"
STARTUP_SECONDS = 30.0
CLIENT_IP = "127.0.0.1"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def api_command(port: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "uvicorn",
        "--factory",
        "app.main:create_app",
        "--no-proxy-headers",
        "--no-access-log",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]


def wait_until_live(process: subprocess.Popen[bytes], base_url: str) -> None:
    """Poll ``/health/live`` until it answers (bounded; no fixed sleep)."""
    deadline = time.monotonic() + STARTUP_SECONDS
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"API process exited with {process.returncode}")
        try:
            if httpx.get(f"{base_url}/health/live", timeout=1.0).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise AssertionError(f"API process on {base_url} did not become live")


@contextmanager
def two_api_processes(url: str) -> Iterator[list[tuple[int, httpx.Client]]]:
    env = {
        **os.environ,
        "APP_ENV": "local",
        "APP_ORIGIN": TEST_APP_ORIGIN,
        "SESSION_COOKIE_SECURE": "false",
        "DATABASE_URL": url,
        "DATABASE_DIRECT_URL": url,
    }
    env.pop("TRUSTED_PROXIES", None)
    processes: list[subprocess.Popen[bytes]] = []
    clients: list[httpx.Client] = []
    try:
        for _ in range(2):
            port = free_port()
            process = subprocess.Popen(  # noqa: S603 - fixed argument list
                api_command(port),
                cwd=API_ROOT,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            processes.append(process)
            base_url = f"http://127.0.0.1:{port}"
            wait_until_live(process, base_url)
            clients.append(
                httpx.Client(
                    base_url=base_url, headers={"Origin": TEST_APP_ORIGIN}, timeout=30
                )
            )
        yield [(p.pid, c) for p, c in zip(processes, clients, strict=True)]
    finally:
        for client in clients:
            client.close()
        for process in processes:
            process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def windows(url: str, policy: str, key: str) -> int:
    rows = run_sql(
        url,
        "SELECT count(DISTINCT window_start) FROM rate_limit_counters"
        " WHERE policy = :p AND key_hash = :h",
        {"p": policy, "h": key_hash(policy, key)},
    )
    return int(rows[0][0])


def in_one_window(
    url: str, policy: str, key: str, run: Callable[[], list[int]]
) -> list[int]:
    """``run()``'s statuses, repeated once if the run crossed a window boundary."""
    for _ in range(2):
        reset_rate_limits(url)
        statuses = run()
        if windows(url, policy, key) == 1:
            return statuses
    pytest.fail(f"{policy}: two runs crossed a window boundary")


def post_login(client: httpx.Client, username: str) -> int:
    response = client.post(LOGIN, json={"username": username, "password": "wrong-x"})
    return response.status_code


def test_two_processes_share_the_username_and_ip_limits(seeded_url):
    with two_api_processes(seeded_url) as processes:
        (first_pid, first), (second_pid, second) = processes
        assert first_pid != second_pid
        alternating = [first, second]

        def username_run() -> list[int]:
            return [
                post_login(alternating[i % 2], "nobody.anywhere") for i in range(11)
            ]

        statuses = in_one_window(
            seeded_url, "login.username", "nobody.anywhere", username_run
        )
        assert statuses == [401] * 10 + [429]

        def ip_run() -> list[int]:
            return [
                post_login(alternating[i % 2], f"user.number{i}") for i in range(50)
            ] + [post_login(first, "fresh.one"), post_login(second, "fresh.two")]

        statuses = in_one_window(seeded_url, "login.ip", CLIENT_IP, ip_run)
        assert statuses == [401] * 50 + [429, 429]
        [row] = run_sql(
            seeded_url,
            "SELECT count FROM rate_limit_counters"
            " WHERE policy = 'login.ip' AND key_hash = :h",
            {"h": key_hash("login.ip", CLIENT_IP)},
        )
        assert row.count == 50
