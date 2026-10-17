"""ASGI entry point.

Run with ``uvicorn --factory app.main:create_app``. Settings are validated when
the app is created, so a missing or invalid value stops the process before it
serves any request.

The lifespan creates the process's one database engine (it does not connect)
and disposes it at shutdown. The API never runs migrations (``make migrate``).
"""

import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import ConfigurationError, Settings, get_settings
from app.core.conventions import install_conventions
from app.core.db import create_engine, create_sessionmaker
from app.core.health import router as health_router
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Honors app.dependency_overrides[get_settings], the test seam of M1-05.
    settings: Settings = app.dependency_overrides.get(get_settings, get_settings)()
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    try:
        get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (see ConfigurationError).
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    configure_logging()
    app = FastAPI(title="Classora API", debug=False, lifespan=lifespan)
    install_conventions(app)
    app.include_router(health_router)
    return app
