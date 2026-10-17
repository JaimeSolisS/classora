"""X-Request-ID acceptance, generation and echo (B1, B2)."""

import uuid

import pytest
from fastapi.testclient import TestClient


def assert_generated(value: str) -> None:
    assert str(uuid.UUID(value, version=4)) == value


def test_valid_inbound_id_is_echoed_unchanged(client: TestClient):
    value = "Qa.req_001-" + "x" * 117  # 128 characters, all allowed
    response = client.get("/health/live", headers={"X-Request-ID": value})

    assert response.headers["x-request-id"] == value


def test_missing_id_is_generated(client: TestClient):
    response = client.get("/health/live")

    assert_generated(response.headers["x-request-id"])


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "a" * 129,
        "x" * 200,
        "bad id",
        "bad\tid",
        "café",
        "a/b",
    ],
)
def test_invalid_inbound_id_is_replaced_not_rejected(
    client: TestClient, capsys: pytest.CaptureFixture[str], invalid: str
):
    raw = invalid.encode("utf-8")
    response = client.get("/health/live", headers=[(b"x-request-id", raw)])

    assert response.status_code == 200
    assert_generated(response.headers["x-request-id"])
    if invalid:
        assert invalid not in response.headers["x-request-id"]
        assert invalid not in capsys.readouterr().out


def test_id_with_newline_is_replaced(client: TestClient):
    # httpx refuses to send a raw newline, so call the middleware directly.
    import asyncio

    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/health/live",
        "raw_path": b"/health/live",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"x-request-id", b"evil\nInjected: 1")],
        "client": ("testclient", 1),
        "server": ("testserver", 80),
        "state": {},
    }
    asyncio.run(client.app(scope, receive, send))

    start = messages[0]
    ids = [v for k, v in start["headers"] if k == b"x-request-id"]
    assert len(ids) == 1
    assert_generated(ids[0].decode())


@pytest.mark.parametrize(
    ("method", "path", "status"),
    [
        ("GET", "/health/live", 200),
        ("GET", "/nope", 404),
        ("POST", "/health/live", 405),
        ("GET", "/query?limit=0", 422),
        ("GET", "/app-error", 423),
        ("GET", "/conflict", 409),
        ("GET", "/boom", 500),
    ],
)
def test_every_response_has_exactly_one_request_id(
    client: TestClient, method: str, path: str, status: int
):
    response = client.request(method, path, headers={"X-Request-ID": "qa-one"})

    assert response.status_code == status
    assert response.headers.get_list("x-request-id") == ["qa-one"]
    if status >= 400:
        assert response.json()["request_id"] == "qa-one"
