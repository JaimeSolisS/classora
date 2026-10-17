"""ASGI entry point.

Run with ``uvicorn --factory app.main:create_app``. Settings are validated when
the app is created, so a missing or invalid value stops the process before it
serves any request.
"""

import sys

from fastapi import FastAPI

from app.core.config import ConfigurationError, get_settings
from app.core.conventions import install_conventions
from app.core.health import router as health_router
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    try:
        get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (see ConfigurationError).
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    configure_logging()
    app = FastAPI(title="Classora API", debug=False)
    install_conventions(app)
    app.include_router(health_router)
    return app
