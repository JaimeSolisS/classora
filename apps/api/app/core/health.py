"""Liveness and readiness endpoints (architecture §9.3).

These live at the application root, not under ``/api/v1``. They are
unauthenticated infrastructure probes and must never expose internal details.
"""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.authz import public
from app.core.db import get_engine
from app.core.errors import AppError, problem_response_doc

router = APIRouter(prefix="/health", tags=["health"])

# Upper bound for the whole check (pool checkout, connect and query); the
# engine's connect timeout (app.core.db) is not longer. When PgBouncer is up but
# PostgreSQL is not, the query waits inside PgBouncer until this timeout cancels
# it, and SQLAlchemy then takes up to 2 more seconds to close that connection,
# so the 503 still comes well within 5 seconds.
READY_TIMEOUT_SECONDS = 2.0


class DatabaseUnavailableError(AppError):
    # Deliberately generic: no DSN, host, driver error text or traceback.
    status = 503
    code = "database_unavailable"
    detail = "Database is not reachable."


class HealthStatus(BaseModel):
    """The success body of both health probes."""

    status: Literal["ok"]


async def _database_is_reachable(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(READY_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


@router.get("/live", dependencies=[public(reason="Liveness probe (architecture §9.3)")])
async def live() -> HealthStatus:
    """The process is up. Never touches the database."""
    return HealthStatus(status="ok")


@router.get(
    "/ready",
    dependencies=[public(reason="Readiness probe (architecture §9.3)")],
    responses={503: problem_response_doc("The database is not reachable.")},
)
async def ready(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> HealthStatus:
    """The process is up and PostgreSQL answers ``SELECT 1`` (pooled engine)."""
    if not await _database_is_reachable(engine):
        raise DatabaseUnavailableError()
    return HealthStatus(status="ok")
