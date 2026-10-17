"""JSON logs, request ID in every line, redaction and access log (B3-B6, E2)."""

import asyncio
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import REDACTED, SENSITIVE_KEY_FRAGMENTS, is_sensitive_key
from tests.unit.conventions_app import LEAK_MARKER, read_log_lines

API_ROOT = Path(__file__).resolve().parents[2]


def access_lines(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line["logger"] == "app.access"]


# --- B3 line shape ------------------------------------------------------------


def test_log_line_shape_outside_a_request(test_app, capsys):
    logging.getLogger("tests.any").info("outside")

    [line] = read_log_lines(capsys)
    assert line["level"] == "INFO"
    assert line["logger"] == "tests.any"
    assert line["message"] == "outside"
    assert line["request_id"] is None
    timestamp = datetime.fromisoformat(line["timestamp"])
    assert timestamp.utcoffset() is not None
    assert timestamp.utcoffset().total_seconds() == 0


def test_exception_adds_formatted_traceback(test_app, capsys):
    try:
        raise ValueError("boom-value")
    except ValueError:
        logging.getLogger("tests.any").exception("failed")

    [line] = read_log_lines(capsys)
    assert line["level"] == "ERROR"
    assert "Traceback" in line["exception"]
    assert "ValueError: boom-value" in line["exception"]


# --- B4 request ID in every line ----------------------------------------------


def test_arbitrary_logger_line_carries_request_id(client: TestClient, capsys):
    response = client.get("/log?tag=one", headers={"X-Request-ID": "qa-log-1"})

    lines = [ln for ln in read_log_lines(capsys) if ln["logger"] == "tests.any"]
    assert [ln["message"] for ln in lines] == ["first", "second"]
    assert {ln["request_id"] for ln in lines} == {response.headers["x-request-id"]}
    assert lines[0]["tag"] == "one"


def test_concurrent_requests_keep_their_own_request_id(test_app: FastAPI, capsys):
    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            return await asyncio.gather(
                c.get("/log?tag=a", headers={"X-Request-ID": "req-a"}),
                c.get("/log?tag=b", headers={"X-Request-ID": "req-b"}),
            )

    responses = asyncio.run(run())

    assert [r.headers["x-request-id"] for r in responses] == ["req-a", "req-b"]
    lines = [ln for ln in read_log_lines(capsys) if ln["logger"] == "tests.any"]
    # The two requests interleave: both "first" lines come before any "second".
    assert [ln["message"] for ln in lines] == ["first", "first", "second", "second"]
    for line in lines:
        assert line["request_id"] == f"req-{line['tag']}"


# --- A5 / E2 the 500 is logged once --------------------------------------------


def test_unhandled_exception_logged_once_with_traceback_and_request_id(
    client: TestClient, capsys
):
    response = client.get("/boom", headers={"X-Request-ID": "qa-boom"})

    assert response.status_code == 500
    lines = read_log_lines(capsys)
    errors = [ln for ln in lines if ln["level"] == "ERROR"]
    assert len(errors) == 1
    [error] = errors
    assert error["request_id"] == "qa-boom"
    assert LEAK_MARKER in error["exception"]
    assert "RuntimeError" in error["exception"]
    assert LEAK_MARKER not in error["message"]
    [access] = access_lines(lines)
    assert access["status"] == 500
    assert access["request_id"] == "qa-boom"


# --- B5 redaction -------------------------------------------------------------


def test_sensitive_extra_keys_are_redacted_at_any_depth(client: TestClient, capsys):
    client.get("/log-extra")

    [line] = [ln for ln in read_log_lines(capsys) if ln["logger"] == "tests.any"]
    raw = json.dumps(line)
    for secret in ("p1", "t2", "t3"):
        assert f'"{secret}"' not in raw
        assert "Bearer" not in raw
    assert line["password"] == REDACTED
    assert line["context"]["Authorization"] == REDACTED
    assert line["context"]["items"] == [{"csrf_token": REDACTED}]
    assert line["user_id"] == "u1"


def test_sensitive_key_rule():
    assert set(SENSITIVE_KEY_FRAGMENTS) >= {
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "api_key",
        "apikey",
        "credential",
        "dsn",
        "database_url",
        "database_direct_url",
    }
    for key in (
        "DATABASE_URL",
        "DATABASE_DIRECT_URL",
        "X-Api-Key",
        "Set-Cookie",
        "refreshToken",
        "db_dsn",
    ):
        assert is_sensitive_key(key), key
    for key in ("user_id", "path", "status", "request_id"):
        assert not is_sensitive_key(key), key


# --- B6 access log ------------------------------------------------------------


def test_one_access_line_per_request_without_query_or_headers(
    client: TestClient, capsys
):
    response = client.get(
        "/query?limit=5&token=qs-secret",
        headers={
            "X-Request-ID": "qa-access",
            "Cookie": "session=cookie-secret",
            "Authorization": "Bearer header-secret",
        },
    )

    assert response.status_code == 200
    # Server-side lines only: httpx (the test client) logs its own request URL.
    lines = [ln for ln in read_log_lines(capsys) if ln["logger"] != "httpx"]
    out = "\n".join(json.dumps(ln) for ln in lines)
    [access] = access_lines(lines)
    assert access["level"] == "INFO"
    assert access["method"] == "GET"
    assert access["path"] == "/query"
    assert access["status"] == 200
    assert isinstance(access["duration_ms"], int | float)
    assert access["request_id"] == "qa-access"
    for forbidden in ("qs-secret", "limit=5", "cookie-secret", "header-secret"):
        assert forbidden not in out
    assert "testclient" not in out  # the client address


@pytest.mark.parametrize(
    ("path", "logged"),
    [
        ("/health/live", False),
        ("/health/ready", False),
        ("/health/ready?fail=true", True),
        ("/nope", True),
    ],
)
def test_successful_health_probes_are_not_access_logged(
    client: TestClient, capsys, path: str, logged: bool
):
    client.get(path)

    assert len(access_lines(read_log_lines(capsys))) == (1 if logged else 0)


def test_post_to_health_is_logged(client: TestClient, capsys):
    client.post("/health/live")

    [access] = access_lines(read_log_lines(capsys))
    assert access["status"] == 405


# --- B3 / B6 the real server process ------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_uvicorn_process_writes_only_json_lines():
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "--factory",
            "app.main:create_app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=API_ROOT,
        env={
            "PATH": os.environ.get("PATH", ""),
            "APP_ENV": "local",
            "DATABASE_URL": "postgresql://u:placeholder-pw@127.0.0.1:1/db",
            "DATABASE_DIRECT_URL": "postgresql://u:placeholder-pw@127.0.0.1:1/db",
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 30
        while True:
            try:
                urllib.request.urlopen(f"{base}/health/live", timeout=1)
                break
            except OSError:
                if time.monotonic() > deadline or process.poll() is not None:
                    raise
                time.sleep(0.1)
        request = urllib.request.Request(
            f"{base}/nope?q=1", headers={"X-Request-ID": "qa-proc"}
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(request, timeout=5)
        assert exc_info.value.headers["X-Request-ID"] == "qa-proc"
    finally:
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=30)

    lines = (stdout + stderr).splitlines()
    assert lines
    parsed = [json.loads(line) for line in lines]  # every line is JSON
    messages = [line["message"] for line in parsed]
    assert "Application startup complete." in messages
    assert any(m.startswith("Finished server process") for m in messages)
    startup = parsed[messages.index("Application startup complete.")]
    assert startup["request_id"] is None
    assert startup["logger"] == "uvicorn.error"
    # One access line, from app.access (uvicorn's is off); the probe is not logged.
    access = [line for line in parsed if "access" in line["logger"]]
    assert len(access) == 1
    assert access[0]["logger"] == "app.access"
    assert access[0]["request_id"] == "qa-proc"
    assert access[0]["path"] == "/nope"
    assert access[0]["status"] == 404
