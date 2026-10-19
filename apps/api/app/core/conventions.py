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
from app.core.security_headers import SecurityHeadersMiddleware


def install_conventions(app: FastAPI) -> None:
    """Problem Details handlers and schema, request ID, access log, 500s,
    and the API security headers on every response."""
    install_exception_handlers(app)
    install_problem_details_openapi(app)
    app.add_middleware(RequestContextMiddleware)
    # Added last, so it is the outer one: it also sees the last-resort 500
    # that RequestContextMiddleware writes.
    app.add_middleware(SecurityHeadersMiddleware)
