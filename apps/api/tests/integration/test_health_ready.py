from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings, get_settings
from app.main import create_app


@pytest.fixture
def client(app_env: None, database_url: str) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="local", database_url=SecretStr(database_url)
    )
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_ready_returns_ok_against_real_database(client: TestClient) -> None:
    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
