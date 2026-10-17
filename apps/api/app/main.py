"""ASGI entry point.

Run with ``uvicorn --factory app.main:create_app``. Settings are validated when
the app is created, so a missing or invalid value stops the process before it
serves any request.
"""

import sys

from fastapi import FastAPI

from app.core.config import ConfigurationError, get_settings
from app.core.health import router as health_router


def create_app() -> FastAPI:
    try:
        get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (see ConfigurationError).
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    app = FastAPI(title="Classora API")
    app.include_router(health_router)
    return app
