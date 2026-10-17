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
    return create_app()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def use_unreachable_database(app: FastAPI) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="local", database_url=SecretStr(UNREACHABLE_DSN)
    )


def test_live_returns_ok(client):
    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_returns_problem_details_when_database_unreachable(app, client):
    use_unreachable_database(app)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Service Unavailable"
    assert body["status"] == 503
    assert body["code"] == "database_unavailable"


def test_ready_503_does_not_leak_connection_details(app, client):
    use_unreachable_database(app)

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
