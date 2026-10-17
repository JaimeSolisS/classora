from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.health import get_database_url
from app.main import app


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    app.dependency_overrides[get_database_url] = lambda: database_url
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_ready_returns_ok_against_real_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
