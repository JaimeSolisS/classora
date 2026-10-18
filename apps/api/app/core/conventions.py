"""Installs the shared API conventions on a FastAPI app.

``app.main.create_app`` calls :func:`install_conventions`; tests apply it to a
test-only ``FastAPI()`` app to mount their own routes.
"""

from fastapi import FastAPI

from app.core.errors import (
    install_exception_handlers,
    install_problem_details_openapi,
)
from app.core.middleware import RequestContextMiddleware


def install_conventions(app: FastAPI) -> None:
    """Problem Details handlers and schema, request ID, access log, 500s."""
    install_exception_handlers(app)
    install_problem_details_openapi(app)
    app.add_middleware(RequestContextMiddleware)
