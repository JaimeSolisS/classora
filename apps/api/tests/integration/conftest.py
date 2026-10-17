"""Testcontainers PostgreSQL for integration tests.

Strategy: one PostgreSQL container per pytest session, started lazily the
first time an integration test requests it and stopped at session end. It is
published on a random host port chosen by Testcontainers, so it never clashes
with the Compose ``db`` and never touches its volume. Unit-only runs start no
container.

Per-test isolation (transaction rollback or truncation) arrives with the
database layer in M1-07.
"""

import os
from collections.abc import Iterator

import pytest
from testcontainers.community.postgres import PostgresContainer

# Socket the Ryuk reaper container mounts, as seen by the Docker daemon (not the
# host). Testcontainers otherwise mounts the host-side client socket, which on
# Docker Desktop for macOS (~/.docker/run/docker.sock) is not mountable and makes
# Ryuk exit at startup. setdefault keeps any value the developer already exports
# (for example rootless Docker).
os.environ.setdefault("TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE", "/var/run/docker.sock")

# Must match the db image in compose.yaml.
# Pin set by _docs/adr/0001-runtime-version-pins.md (accepted).
POSTGRES_IMAGE = "postgres:18"


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
    """Plain ``postgresql://`` DSN (no SQLAlchemy driver suffix) for asyncpg."""
    return postgres_container.get_connection_url(driver=None)
