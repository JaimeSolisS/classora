"""Testcontainers PostgreSQL for integration tests.

Strategy: one PostgreSQL container per pytest session, started lazily the
first time an integration test requests it and stopped at session end. It is
published on a random host port chosen by Testcontainers, so it never clashes
with the Compose ``db`` and never touches its volume. Unit-only runs start no
container.

Per-test isolation: a test that writes data or runs migrations asks for
``fresh_database_url`` (an empty database created in the session container
just for that test and dropped with ``FORCE`` afterwards) or ``tables_url``
(the same, with the test-only tables from ``tests/integration/models.py``
created). Tests therefore share no data and can run in any order. Only the
read-only readiness tests use the container's default database.

Async tests run on the anyio pytest plugin (``pytest.mark.anyio``, asyncio
backend). Setup and checks that run outside a test's event loop use
``run_sql`` / ``asyncio.run`` with a short-lived ``NullPool`` engine.
"""

import asyncio
import os
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import make_url, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from app.core.config import Settings
from app.core.db import async_database_url
from tests.integration.models import TestBase

# Socket the Ryuk reaper container mounts, as seen by the Docker daemon (not the
# host). Testcontainers otherwise mounts the host-side client socket, which on
# Docker Desktop for macOS (~/.docker/run/docker.sock) is not mountable and makes
# Ryuk exit at startup. setdefault keeps any value the developer already exports
# (for example rootless Docker).
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

# Must match the db image in compose.yaml.
# Pin set by _docs/adr/0001-runtime-version-pins.md (accepted).
POSTGRES_IMAGE = "postgres:18"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer(
        POSTGRES_IMAGE,
        username="classora_test",
        password="classora_test",
        dbname="classora_test",
        driver=None,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    """Plain ``postgresql://`` DSN of the container's default database."""
    return postgres_container.get_connection_url(driver=None)


def settings_for(dsn: str) -> Settings:
    return Settings(app_env="local", database_url=SecretStr(dsn))


def short_lived_engine(dsn: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(
        async_database_url(settings_for(dsn)), poolclass=pool.NullPool, **kwargs
    )


def run_sql(dsn: str, sql: str, *, autocommit: bool = False) -> list[Any]:
    """Run one statement in its own connection and return all rows (if any)."""

    async def _run() -> list[Any]:
        options = {"isolation_level": "AUTOCOMMIT"} if autocommit else {}
        engine = short_lived_engine(dsn, **options)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(sql))
                return list(result.all()) if result.returns_rows else []
        finally:
            await engine.dispose()

    return asyncio.run(_run())


@pytest.fixture
def fresh_database_url(database_url: str) -> Iterator[str]:
    """An empty database created for this test only (dropped afterwards)."""
    name = f"test_{uuid.uuid4().hex}"
    run_sql(database_url, f'CREATE DATABASE "{name}"', autocommit=True)
    try:
        yield (
            make_url(database_url)
            .set(database=name)
            .render_as_string(hide_password=False)
        )
    finally:
        run_sql(database_url, f'DROP DATABASE "{name}" WITH (FORCE)', autocommit=True)


@pytest.fixture
def tables_url(fresh_database_url: str) -> str:
    """A fresh database with the test-only tables created."""

    async def _create() -> None:
        engine = short_lived_engine(fresh_database_url)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(TestBase.metadata.create_all)
        finally:
            await engine.dispose()

    asyncio.run(_create())
    return fresh_database_url
