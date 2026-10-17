"""Fixtures for the test-only conventions app (see ``conventions_app.py``)."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.logging import configure_logging
from tests.unit.conventions_app import build_test_app


@pytest.fixture
def test_app() -> FastAPI:
    configure_logging()
    return build_test_app()


@pytest.fixture
def client(test_app: FastAPI) -> Iterator[TestClient]:
    with TestClient(test_app, raise_server_exceptions=False) as test_client:
        yield test_client
