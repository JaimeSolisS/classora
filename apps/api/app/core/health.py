"""Liveness and readiness endpoints (architecture §9.3).

These live at the application root, not under ``/api/v1``. They are
unauthenticated infrastructure probes and must never expose internal details.
"""

import asyncio
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.core.config import Settings, get_settings

router = APIRouter(prefix="/health", tags=["health"])

CONNECT_TIMEOUT_SECONDS = 2.0
QUERY_TIMEOUT_SECONDS = 2.0

PROBLEM_JSON = "application/problem+json"


def get_database_url(settings: Annotated[Settings, Depends(get_settings)]) -> str:
    """Return the database DSN from the validated settings."""
    return settings.database_url.get_secret_value()


async def _database_is_reachable(dsn: str) -> bool:
    try:
        conn = await asyncpg.connect(dsn, timeout=CONNECT_TIMEOUT_SECONDS)
    except Exception:
        return False
    try:
        await conn.fetchval("SELECT 1", timeout=QUERY_TIMEOUT_SECONDS)
        return True
    except Exception:
        return False
    finally:
        try:
            await asyncio.wait_for(conn.close(), timeout=QUERY_TIMEOUT_SECONDS)
        except Exception:
            conn.terminate()


@router.get("/live")
async def live() -> dict[str, str]:
    """The process is up. Never touches the database."""
    return {"status": "ok"}


@router.get("/ready", response_model=None)
async def ready(
    database_url: str = Depends(get_database_url),
) -> dict[str, str] | JSONResponse:
    """The process is up and PostgreSQL answers ``SELECT 1``."""
    if await _database_is_reachable(database_url):
        return {"status": "ok"}
    # RFC 9457 Problem Details. Deliberately generic: no DSN, host, driver
    # error text or traceback. request_id is added with the shared handler (M1-09).
    return JSONResponse(
        status_code=503,
        media_type=PROBLEM_JSON,
        content={
            "type": "about:blank",
            "title": "Service Unavailable",
            "status": 503,
            "code": "database_unavailable",
            "detail": "Database is not reachable.",
        },
    )
