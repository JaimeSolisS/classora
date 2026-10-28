"""ASGI entry point.

Run with ``uvicorn --factory app.main:create_app``. Settings are validated when
the app is created, so a missing or invalid value stops the process before it
serves any request.

The lifespan creates the process's one database engine and opens the job app
used to defer background jobs (neither connects yet, so the API starts while
the database is down), and starts the live-notification listener (#42: one
direct ``LISTEN`` connection, retried in the background while the database is
down); all are closed at shutdown. The open notification streams end as soon
as a shutdown signal arrives (``app.core.shutdown``), before uvicorn waits for
open responses. The API never runs migrations (``make migrate``).

The demo job route (``/api/v1/dev/demo-jobs``) and the demo notification
route (``/api/v1/dev/notifications``, #41) exist only when ``APP_ENV`` is
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
from app.core.db import (
    async_direct_database_url,
    create_direct_engine,
    create_engine,
    create_sessionmaker,
)
from app.core.demo_jobs import router as demo_jobs_router
from app.core.health import router as health_router
from app.core.jobs import DEMO_ENVIRONMENTS, create_api_job_app
from app.core.logging import configure_logging
from app.core.shutdown import on_shutdown_signal
from app.modules.administration.approvals_router import router as approvals_router
from app.modules.administration.live import (
    NotificationListener,
    install_notification_hub,
    notification_hub_of,
    register_stream_session_check,
)
from app.modules.administration.notifications_dev import (
    register_dev_notification_type,
)
from app.modules.administration.notifications_dev import (
    router as dev_notifications_router,
)
from app.modules.administration.notifications_router import (
    router as notifications_router,
)
from app.modules.administration.policies_router import (
    router as scheduling_policies_router,
)
from app.modules.administration.router import router as administration_router
from app.modules.administration.settings_router import (
    router as business_settings_router,
)
from app.modules.catalog.classrooms_router import router as classrooms_router
from app.modules.catalog.periods_router import router as periods_router
from app.modules.catalog.router import router as catalog_router
from app.modules.courses.router import router as courses_router
from app.modules.families import service as families_service
from app.modules.families.consent_router import router as consent_router
from app.modules.families.groupings_router import router as groupings_router
from app.modules.families.guardians_router import router as guardians_router
from app.modules.families.router import router as families_router
from app.modules.identity.invitations_router import router as invitations_router
from app.modules.identity.privacy_router import router as privacy_router
from app.modules.identity.recovery_router import router as recovery_router
from app.modules.identity.registrations_router import (
    router as registrations_router,
)
from app.modules.identity.router import router as identity_router
from app.modules.identity.service import (
    is_session_live,
    register_student_login_gate,
    resolve_session_principal,
)
from app.modules.identity.user_admin_router import router as user_admin_router
from app.modules.scheduling.attendance_router import router as attendance_router
from app.modules.scheduling.change_requests_router import (
    router as change_requests_router,
)
from app.modules.scheduling.requests_router import (
    requests_router as session_requests_router,
)
from app.modules.scheduling.requests_router import (
    slots_router as available_slots_router,
)
from app.modules.scheduling.router import (
    offers_router as assignment_offers_router,
)
from app.modules.scheduling.router import options_router as schedule_options_router
from app.modules.scheduling.router import router as class_sessions_router
from app.modules.scheduling.router import (
    series_router as class_series_router,
)
from app.modules.scheduling.router import (
    suggestions_router as tutor_suggestions_router,
)
from app.modules.scheduling.schedule_router import router as schedules_router
from app.modules.staff.router import router as tutors_router
from app.wiring import register_approval_wiring
from app.workflows.assignments_router import router as assignments_router
from app.workflows.assignments_router import (
    student_router as student_assignments_router,
)
from app.workflows.change_requests_router import (
    router as change_request_review_router,
)
from app.workflows.cohorts_router import router as cohorts_router
from app.workflows.evaluations_router import (
    evaluation_router as evaluations_router,
)
from app.workflows.evaluations_router import (
    session_router as evaluation_context_router,
)
from app.workflows.evaluations_router import (
    student_router as student_evaluations_router,
)
from app.workflows.evaluations_router import (
    template_router as evaluation_templates_router,
)
from app.workflows.groups_router import router as groups_router
from app.workflows.learning_plans_router import (
    milestone_router as learning_plan_milestones_router,
)
from app.workflows.learning_plans_router import (
    plan_router as learning_plans_router,
)
from app.workflows.learning_plans_router import (
    student_router as student_learning_plans_router,
)
from app.workflows.overdue_closeouts_router import (
    router as overdue_closeouts_router,
)
from app.workflows.price_catalog_router import router as price_catalog_router
from app.workflows.progress_dashboard_router import (
    student_router as student_progress_dashboard_router,
)
from app.workflows.progress_report_schedules_router import (
    router as progress_report_schedules_router,
)
from app.workflows.progress_report_schedules_router import (
    student_router as student_progress_report_schedules_router,
)
from app.workflows.progress_reports_router import router as progress_reports_router
from app.workflows.progress_reports_router import (
    student_router as student_progress_reports_router,
)
from app.workflows.session_closeout_router import (
    router as session_closeout_router,
)
from app.workflows.session_notes_router import note_router as notes_router
from app.workflows.session_notes_router import (
    session_router as session_notes_router,
)
from app.workflows.session_notes_router import (
    student_router as student_notes_router,
)
from app.workflows.session_requests_router import (
    router as session_request_review_router,
)


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
    # The one direct LISTEN connection (ADR-0003; pool of 1, never PgBouncer).
    hub = notification_hub_of(app)
    listener = NotificationListener(
        hub,
        create_direct_engine(
            async_direct_database_url(settings), pool_size=1, max_overflow=0
        ),
    )
    listener.start()
    app.state.notification_listener = listener
    try:
        # At SIGTERM/SIGINT, before uvicorn waits for open responses: end the
        # streams cleanly, so proxies see a normal end and the browser
        # reconnects (#42, QA D1).
        with on_shutdown_signal(hub.close):
            yield
    finally:
        hub.close()
        await listener.stop()
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
    ``get_current_principal`` resolves the session cookie (#23), and the
    families student-login gate (#35 B1; activating a student fails closed
    without it), the approval types and the account-reference resolver of
    the approval inbox (#40 D3, E4), and installs
    the ``Origin`` check on every unsafe request as an app-level dependency, so
    it runs before authentication (#24).
    ``include_dev_routes`` mounts the local/DEV demo job and demo notification
    routes (and registers the ``dev.demo`` notification type); the exported
    contract (``app.core.openapi``) never includes them.
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
    # The families consent check for every student activation (#35 B1).
    register_student_login_gate(families_service.STUDENT_LOGIN_GATE)
    register_approval_wiring()
    install_notification_hub(app)
    register_stream_session_check(app, is_session_live)
    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(invitations_router)
    app.include_router(privacy_router)
    app.include_router(recovery_router)
    app.include_router(registrations_router)
    app.include_router(user_admin_router)
    app.include_router(administration_router)
    app.include_router(approvals_router)
    app.include_router(notifications_router)
    app.include_router(business_settings_router)
    app.include_router(scheduling_policies_router)
    app.include_router(catalog_router)
    app.include_router(periods_router)
    app.include_router(classrooms_router)
    app.include_router(families_router)
    app.include_router(guardians_router)
    app.include_router(groupings_router)
    app.include_router(consent_router)
    app.include_router(tutors_router)
    app.include_router(courses_router)
    app.include_router(class_sessions_router)
    app.include_router(attendance_router)
    app.include_router(session_closeout_router)
    app.include_router(overdue_closeouts_router)
    app.include_router(session_notes_router)
    app.include_router(student_notes_router)
    app.include_router(notes_router)
    app.include_router(evaluation_templates_router)
    app.include_router(evaluations_router)
    app.include_router(student_evaluations_router)
    app.include_router(evaluation_context_router)
    app.include_router(student_learning_plans_router)
    app.include_router(learning_plans_router)
    app.include_router(learning_plan_milestones_router)
    app.include_router(assignments_router)
    app.include_router(student_assignments_router)
    app.include_router(progress_reports_router)
    app.include_router(student_progress_reports_router)
    app.include_router(student_progress_report_schedules_router)
    app.include_router(progress_report_schedules_router)
    app.include_router(student_progress_dashboard_router)
    app.include_router(schedule_options_router)
    app.include_router(schedules_router)
    app.include_router(tutor_suggestions_router)
    app.include_router(class_series_router)
    app.include_router(assignment_offers_router)
    app.include_router(groups_router)
    app.include_router(cohorts_router)
    app.include_router(price_catalog_router)
    app.include_router(available_slots_router)
    app.include_router(session_requests_router)
    app.include_router(session_request_review_router)
    app.include_router(change_requests_router)
    app.include_router(change_request_review_router)
    if include_dev_routes:
        app.include_router(demo_jobs_router)
        # The dev.demo notification type exists only with its route (#41 D1).
        register_dev_notification_type()
        app.include_router(dev_notifications_router)
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
