"""Liveness and readiness endpoints (architecture §9.3).

These live at the application root, not under ``/api/v1``. They are
unauthenticated infrastructure probes and must never expose internal details.
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.db import get_engine
from app.core.errors import AppError

router = APIRouter(prefix="/health", tags=["health"])

# Upper bound for the whole check (pool checkout, connect and query). The
# engine's connect timeout (app.core.db) is shorter.
READY_TIMEOUT_SECONDS = 4.0


class DatabaseUnavailableError(AppError):
    # Deliberately generic: no DSN, host, driver error text or traceback.
    status = 503
    code = "database_unavailable"
    detail = "Database is not reachable."


async def _database_is_reachable(engine: AsyncEngine) -> bool:
    try:
        async with asyncio.timeout(READY_TIMEOUT_SECONDS):
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


@router.get("/live")
async def live() -> dict[str, str]:
    """The process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(engine: Annotated[AsyncEngine, Depends(get_engine)]) -> dict[str, str]:
    """The process is up and PostgreSQL answers ``SELECT 1`` (pooled engine)."""
    if not await _database_is_reachable(engine):
        raise DatabaseUnavailableError()
    return {"status": "ok"}
