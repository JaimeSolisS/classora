"""ASGI entry point.

Run with ``uvicorn --factory app.main:create_app``. Settings are validated when
the app is created, so a missing or invalid value stops the process before it
serves any request.

The lifespan creates the process's one database engine and opens the job app
used to defer background jobs (neither connects yet, so the API starts while
the database is down); both are closed at shutdown. The API never runs
migrations (``make migrate``).

The demo job route (``/api/v1/dev/demo-jobs``) exists only when ``APP_ENV`` is
``local`` or ``dev``.

:func:`build_app` assembles the app (metadata, conventions, routers) without
reading settings; ``create_app()`` and the OpenAPI export
(``app.core.openapi``) both use it, so the exported document describes the
same routes as the running app.
"""

import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import Depends, FastAPI

from app.core.authz import register_principal_resolver
from app.core.config import ConfigurationError, Settings, get_settings
from app.core.conventions import install_conventions
from app.core.csrf import require_trusted_origin
from app.core.db import create_engine, create_sessionmaker
from app.core.demo_jobs import router as demo_jobs_router
from app.core.health import router as health_router
from app.core.jobs import DEMO_ENVIRONMENTS, create_api_job_app
from app.core.logging import configure_logging
from app.modules.administration.router import router as administration_router
from app.modules.catalog.router import router as catalog_router
from app.modules.identity.invitations_router import router as invitations_router
from app.modules.identity.recovery_router import router as recovery_router
from app.modules.identity.registrations_router import (
    router as registrations_router,
)
from app.modules.identity.router import router as identity_router
from app.modules.identity.service import resolve_session_principal
from app.modules.identity.user_admin_router import router as user_admin_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Honors app.dependency_overrides[get_settings], the test seam of M1-05.
    settings: Settings = app.dependency_overrides.get(get_settings, get_settings)()
    engine = create_engine(settings)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    # Defers jobs through PgBouncer; its pool opens empty (no connection yet).
    job_app = create_api_job_app(settings)
    await job_app.open_async()
    app.state.job_app = job_app
    try:
        yield
    finally:
        await job_app.close_async()
        await engine.dispose()


# OpenAPI ``info``: fixed in code (never from git or the date), so the exported
# document is deterministic.
API_TITLE = "Classora API"
API_VERSION = "0.1.0"


def build_app(
    *,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    include_dev_routes: bool = False,
) -> FastAPI:
    """The app without settings validation: metadata, conventions, routers.

    Registers the identity module's session resolver, so
    ``get_current_principal`` resolves the session cookie (#23), and installs
    the ``Origin`` check on every unsafe request as an app-level dependency, so
    it runs before authentication (#24).
    ``include_dev_routes`` mounts the local/DEV demo job route; the exported
    contract (``app.core.openapi``) never includes it.
    """
    app = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        debug=False,
        lifespan=lifespan,
        dependencies=[Depends(require_trusted_origin)],
    )
    install_conventions(app)
    register_principal_resolver(app, resolve_session_principal)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(invitations_router)
    app.include_router(recovery_router)
    app.include_router(registrations_router)
    app.include_router(user_admin_router)
    app.include_router(administration_router)
    app.include_router(catalog_router)
    if include_dev_routes:
        app.include_router(demo_jobs_router)
    return app


def create_app() -> FastAPI:
    try:
        settings = get_settings()
    except ConfigurationError as exc:
        # Short message, no traceback, no secret (see ConfigurationError).
        print(exc, file=sys.stderr)
        raise SystemExit(1) from None

    configure_logging()
    return build_app(
        lifespan=lifespan,
        include_dev_routes=settings.app_env in DEMO_ENVIRONMENTS,
    )
