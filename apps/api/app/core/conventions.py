"""Installs the shared API conventions on a FastAPI app.

``app.main.create_app`` calls :func:`install_conventions`; tests apply it to a
test-only ``FastAPI()`` app to mount their own routes.
"""

from fastapi import FastAPI

from app.core.errors import install_exception_handlers
from app.core.middleware import RequestContextMiddleware


def install_conventions(app: FastAPI) -> None:
    """Problem Details handlers, request ID, access log and 500 handling."""
    install_exception_handlers(app)
    app.add_middleware(RequestContextMiddleware)
