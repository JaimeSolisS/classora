"""A test-only app with the shared API conventions and probe routes.

Used by the fixtures in ``conftest.py`` and imported by the tests.

The production app (``app.main.create_app``) gets none of these routes.
"""

import asyncio
import json
import logging
from typing import Annotated, Any

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.conventions import install_conventions
from app.core.errors import AppError, VersionConflictError
from app.core.health import DatabaseUnavailableError
from app.core.pagination import (
    CursorPage,
    CursorPagination,
    OffsetPage,
    OffsetPagination,
    build_cursor_page,
    decode_cursor,
)

LEAK_MARKER = "leak-marker-XYZ"


class Signup(BaseModel):
    password: str
    age: int


class Line(BaseModel):
    amount: int = Field(ge=0)


class Order(BaseModel):
    items: list[Line]


class Row(BaseModel):
    id: int
    rank: int


class ThingLockedError(AppError):
    status = 423
    code = "thing_locked"
    detail = "The thing is locked."


# 30 rows ordered by (rank, id); ranks repeat so ties cross page boundaries.
ROWS = [Row(id=i, rank=i // 4) for i in range(1, 31)]


def build_test_app() -> FastAPI:
    app = FastAPI()
    install_conventions(app)
    any_logger = logging.getLogger("tests.any")

    @app.post("/signup")
    async def signup(body: Signup) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/orders")
    async def orders(body: Order) -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/query")
    async def query(limit: Annotated[int, Query(ge=1)]) -> dict[str, int]:
        return {"limit": limit}

    @app.get("/path/{number}")
    async def path(number: int) -> dict[str, int]:
        return {"number": number}

    @app.get("/header")
    async def header(x_count: Annotated[int, Header()]) -> dict[str, int]:
        return {"count": x_count}

    @app.get("/app-error")
    async def app_error() -> None:
        raise ThingLockedError()

    @app.get("/app-error-fields")
    async def app_error_fields() -> None:
        raise AppError(
            "Custom detail.",
            errors=[{"path": ["body", "name"], "code": "taken", "message": "Taken."}],
        )

    @app.get("/conflict")
    async def conflict() -> None:
        raise VersionConflictError()

    @app.get("/http-exception")
    async def http_exception() -> None:
        raise HTTPException(
            status_code=418, detail="internal detail", headers={"X-Extra": "kept"}
        )

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError(LEAK_MARKER)

    @app.get("/log")
    async def log(tag: str = "") -> dict[str, str]:
        any_logger.info("first", extra={"tag": tag})
        await asyncio.sleep(0.05)
        any_logger.info("second", extra={"tag": tag})
        return {"status": "ok"}

    @app.get("/log-extra")
    async def log_extra() -> dict[str, str]:
        any_logger.info(
            "with extra",
            extra={
                "password": "p1",
                "context": {
                    "Authorization": "Bearer t2",
                    "items": [{"csrf_token": "t3"}],
                },
                "user_id": "u1",
            },
        )
        return {"status": "ok"}

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    async def ready(fail: bool = False) -> dict[str, str]:
        if fail:
            raise DatabaseUnavailableError()
        return {"status": "ok"}

    @app.get("/offset")
    async def offset(page: OffsetPagination) -> OffsetPage[Row]:
        items = ROWS[page.offset : page.offset + page.limit]
        return OffsetPage(
            items=items, total=len(ROWS), limit=page.limit, offset=page.offset
        )

    @app.get("/cursor")
    async def cursor(page: CursorPagination) -> CursorPage[Row]:
        rows = ROWS
        if page.cursor is not None:
            rank, row_id = decode_cursor(page.cursor, length=2)
            if not isinstance(rank, int) or not isinstance(row_id, int):
                raise ValueError("unexpected cursor values")
            rows = [r for r in ROWS if (r.rank, r.id) > (rank, row_id)]
        return build_cursor_page(
            rows[: page.limit + 1], limit=page.limit, key=lambda r: (r.rank, r.id)
        )

    return app


def read_log_lines(capsys: Any) -> list[dict[str, Any]]:
    """Every captured stdout line, parsed as JSON (fails on a non-JSON line)."""
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]
