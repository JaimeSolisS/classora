"""Request traffic through PgBouncer in transaction mode (M1-08 F1).

50 concurrent requests, each a Unit of Work with two different parameterized
queries, go through a PgBouncer whose server pool is smaller than the API's
client pool. They must all succeed with no prepared-statement error (asyncpg
statement cache off, unique statement names: ADR-0003), and they must share
the few server connections (distinct ``pg_backend_pid()`` <= pool size).

Mutation check: without the PgBouncer settings in ``engine_options()`` this
test fails with ``prepared statement "__asyncpg_stmt_..." already exists``
(or ``does not exist``).
"""

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.conventions import install_conventions
from app.core.db import async_database_url, create_sessionmaker, engine_options
from app.core.uow import UnitOfWorkDep
from tests.integration.conftest import PGBOUNCER_POOL_SIZE, settings_for

pytestmark = pytest.mark.anyio

CONCURRENT_REQUESTS = 50
# Client-side pool: at least 20 simultaneous checkouts, far above the server pool.
CLIENT_POOL_SIZE = 25

router = APIRouter()


@router.get("/backend/{n}")
async def backend(n: int, uow: UnitOfWorkDep) -> dict[str, int]:
    doubled = await uow.session.scalar(text("SELECT CAST(:n AS integer) * 2"), {"n": n})
    # Holds the transaction (and its server connection) briefly, so requests overlap.
    pid = await uow.session.scalar(
        text("SELECT pg_backend_pid() FROM pg_sleep(CAST(:delay AS float8))"),
        {"delay": 0.05},
    )
    assert doubled == 2 * n
    assert pid is not None
    return {"n": n, "pid": pid}


@pytest.fixture
async def client(pgbouncer_url: str) -> AsyncIterator[httpx.AsyncClient]:
    # The app's request-engine options (engine_options()), with a pool large
    # enough for the concurrency below.
    engine = create_async_engine(
        async_database_url(settings_for(pgbouncer_url)),
        pool_size=CLIENT_POOL_SIZE,
        max_overflow=0,
        **engine_options(),
    )
    app = FastAPI()
    install_conventions(app)
    app.include_router(router)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http_client:
            yield http_client
    finally:
        await engine.dispose()


async def test_concurrent_requests_through_pgbouncer_share_server_connections(
    client: httpx.AsyncClient,
):
    responses = await asyncio.gather(
        *(client.get(f"/backend/{n}") for n in range(CONCURRENT_REQUESTS))
    )

    failures = [(r.status_code, r.text) for r in responses if r.status_code != 200]
    assert failures == []
    bodies = [r.json() for r in responses]
    assert sorted(b["n"] for b in bodies) == list(range(CONCURRENT_REQUESTS))
    pids = {b["pid"] for b in bodies}
    assert 1 <= len(pids) <= PGBOUNCER_POOL_SIZE
