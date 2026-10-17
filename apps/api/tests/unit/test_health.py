import socket
import time
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app

# Closed local port: connection is refused immediately.
UNREACHABLE_PASSWORD = "not-a-real-secret-pw"
UNREACHABLE_DSN = (
    f"postgresql://leakcheck_user:{UNREACHABLE_PASSWORD}@127.0.0.1:1/leakcheck_db"
)


@pytest.fixture
def app(app_env: None) -> FastAPI:
    app = create_app()
    # Before the lifespan runs, so the engine is built from this DSN.
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="local", database_url=SecretStr(UNREACHABLE_DSN)
    )
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_live_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_problem_details_when_database_unreachable(client):
    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Service Unavailable"
    assert body["status"] == 503
    assert body["code"] == "database_unavailable"
    assert body["detail"] == "Database is not reachable."


def test_ready_uses_the_lifespan_engine(app, client):
    # One engine per process, built from the (overridden) settings.
    engine = app.state.engine
    assert engine.url.drivername == "postgresql+asyncpg"
    assert engine.url.database == "leakcheck_db"

    assert client.get("/health/ready").status_code == 503
    assert engine.pool.checkedout() == 0


def test_ready_503_within_5_seconds_when_database_never_answers(app_env):
    # A listener that accepts TCP connections but never speaks the protocol:
    # the bounded connect timeout turns it into a 503 instead of a hang.
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen(8)
        port = silent.getsockname()[1]
        app = create_app()
        app.dependency_overrides[get_settings] = lambda: Settings(
            app_env="local",
            database_url=SecretStr(f"postgresql://u:pw@127.0.0.1:{port}/db"),
        )
        with TestClient(app) as test_client:
            started = time.monotonic()
            response = test_client.get("/health/ready")
            elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert elapsed < 5


def test_app_starts_without_database_and_never_connects(app):
    # Creating the app and its engine opens no connection (A3).
    with TestClient(app) as test_client:
        assert test_client.get("/health/live").status_code == 200
        assert app.state.engine.pool.checkedout() == 0


def test_ready_503_does_not_leak_connection_details(client):
    response = client.get("/health/ready")

    assert response.status_code == 503
    exposed = response.text + " ".join(f"{k}: {v}" for k, v in response.headers.items())
    for secret in (
        UNREACHABLE_DSN,
        UNREACHABLE_PASSWORD,
        "leakcheck_user",
        "leakcheck_db",
        "127.0.0.1",
        "Traceback",
    ):
        assert secret not in exposed
