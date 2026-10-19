"""``/api/v1/auth``: login, logout and the signed-in user (#23).

The session lives in an HTTP-only cookie (``app.modules.identity.sessions``);
the server stores only the token's hash. Login and ``/me`` return the session's
CSRF token (``app.core.csrf``, #24); every unsafe request needs the app's
``Origin``, and logout also the ``X-CSRF-Token`` header.

``POST /reauthenticate`` (#26; ADR-0007 D10) confirms the signed-in user's
password and opens a 5-minute window on the current session for the routes
declared with ``app.core.reauth.require_recent_auth``.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    AuthenticationRequiredError,
    Principal,
    ResolvedSession,
    authenticated,
    get_current_session,
    public,
)
from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.identity import auth, sessions
from app.modules.identity.errors import InvalidCredentialsError, ReauthFailedError
from app.modules.identity.schemas import (
    LoginRequest,
    ReauthenticateRequest,
    ReauthenticateResponse,
    SessionUser,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionmakerDep = Annotated[async_sessionmaker[AsyncSession], Depends(get_sessionmaker)]

LOGIN_PUBLIC_REASON = (
    "Signing in needs no session; CSRF protection for login is the Origin"
    " check plus SameSite (#24)"
)


@router.post(
    "/login",
    dependencies=[public(reason=LOGIN_PUBLIC_REASON)],
    responses={
        401: problem_response_doc("The username or password is incorrect."),
        **CSRF_RESPONSES,
    },
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    uow: UnitOfWorkDep,
    sessionmaker: SessionmakerDep,
    settings: SettingsDep,
) -> SessionUser:
    """Sign in with a username and password; sets the session cookie.

    Every login issues a new session; a live session on the request's cookie
    is revoked. Any failure is the same ``401 invalid_credentials``. The body
    carries the new session's ``csrf_token``. Needs the app's ``Origin``.
    """
    result = await auth.login(
        uow.session,
        request,
        settings,
        username=body.username,
        password=body.password,
    )
    if isinstance(result, auth.LoginFailure):
        # The failed attempt writes nothing, so end the request's transaction
        # first: its connection goes back to the pool before the event's own
        # Unit of Work takes one. A failed login never holds two connections
        # (otherwise concurrent failures could exhaust the PgBouncer pool).
        await uow.session.rollback()
        # Committed on its own: the 401 rolls the request's Unit of Work back.
        await auth.record_failed_login(sessionmaker, result)
        raise InvalidCredentialsError()
    # Max-Age = the session's absolute timeout (#25), never longer.
    lifetime = result.limits.absolute
    sessions.set_session_cookie(response, settings, result.token, max_age=lifetime)
    return auth.session_user(result.user, result.principal, result.token)


@router.post("/logout", status_code=204, responses={**AUTH_RESPONSES, **CSRF_RESPONSES})
async def logout(
    principal: Annotated[Principal, authenticated()],
    request: Request,
    response: Response,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> None:
    """Sign out: revoke the current session and clear the cookie.

    Needs the app's ``Origin`` and the session's ``X-CSRF-Token``.
    """
    await auth.logout(uow.session, request, settings, principal)
    sessions.clear_session_cookie(response, settings)


@router.get("/me", responses=AUTH_RESPONSES)
async def me(
    principal: Annotated[Principal, authenticated()],
    request: Request,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> SessionUser:
    """The signed-in user, their roles, their effective permissions and the
    session's CSRF token (the same value for the whole session)."""
    return await auth.current_session_user(uow.session, request, settings, principal)


@router.post(
    "/reauthenticate",
    responses={
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The password is incorrect (reauth_failed), or a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
    },
)
async def reauthenticate(
    principal: Annotated[Principal, authenticated()],
    resolved: Annotated[ResolvedSession, Depends(get_current_session)],
    body: ReauthenticateRequest,
    uow: UnitOfWorkDep,
    sessionmaker: SessionmakerDep,
) -> ReauthenticateResponse:
    """Confirm the signed-in user's password for sensitive actions.

    On success the **current** session counts as reauthenticated for 5
    minutes from now (activity does not extend it; a later reauthentication
    restarts it). A wrong password is ``403 reauth_failed``; the session stays
    signed in and its window is unchanged. Needs the app's ``Origin`` and the
    session's ``X-CSRF-Token``.
    """
    if resolved.session_id is None:  # pragma: no cover - the resolver sets it
        raise AuthenticationRequiredError()
    result = await auth.reauthenticate(
        uow.session, principal, resolved.session_id, body.password
    )
    if isinstance(result, auth.ReauthFailure):
        # As for a failed login: end the request's transaction (it wrote
        # nothing that must survive) so its connection goes back to the pool
        # before the event's own Unit of Work takes one; then commit the event
        # on its own, because the 403 rolls the request's Unit of Work back.
        await uow.session.rollback()
        await auth.record_failed_reauth(sessionmaker, result)
        raise ReauthFailedError()
    return ReauthenticateResponse(reauthenticated_until=result)
