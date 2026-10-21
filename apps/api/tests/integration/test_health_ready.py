from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app
from tests.integration.conftest import run_sql, settings_for


@pytest.fixture
def app(app_env: None, database_url: str) -> Iterator[FastAPI]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings_for(database_url)
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_ready_returns_ok_against_real_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_uses_a_pooled_engine_connection(app: FastAPI, client: TestClient):
    for _ in range(3):
        assert client.get("/health/ready").status_code == 200

    pool = app.state.engine.pool
    assert pool.checkedout() == 0
    # The same pooled connection is reused, not a new connection per check.
    assert pool.checkedin() == 1


def test_ready_recovers_after_connections_are_killed(
    app: FastAPI, client: TestClient, database_url: str
):
    assert client.get("/health/ready").status_code == 200

    # What a database restart does to pooled connections.
    run_sql(
        database_url,
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        "WHERE pid <> pg_backend_pid() AND datname = current_database()",
    )

    assert client.get("/health/ready").status_code == 200
