"""``/api/v1/auth``: login, logout and the signed-in user (#23).

The session lives in an HTTP-only cookie (``app.modules.identity.sessions``);
the server stores only the token's hash. Login and ``/me`` return the session's
CSRF token (``app.core.csrf``, #24); every unsafe request needs the app's
``Origin``, and logout also the ``X-CSRF-Token`` header.

``POST /reauthenticate`` (#26; ADR-0007 D10) confirms the signed-in user's
password and opens a 5-minute window on the current session for the routes
declared with ``app.core.reauth.require_recent_auth``.

``PATCH /me/preferences`` (#36) stores the signed-in user's UI language and
theme; login, ``/me`` and the password change return them in
``SessionUser.preferences``.

``POST /password`` (#30; ADR-0007 D8, D14) is the self-service password
change: the current password is the proof (no reauthentication window). It
rotates the session and returns the new CSRF token.

Rate limits (#27; ``app.core.ratelimit``): login takes one ``login.ip`` unit
per request and reserves ``login.username`` before the password check;
reauthentication and the password change reserve ``reauth.user`` (one shared
budget). Over a limit the answer is ``429 rate_limited`` without any password
verification.
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
from app.core.ratelimit import RATE_LIMIT_RESPONSES, RateLimiter, rate_limit
from app.core.uow import UnitOfWorkDep
from app.modules.identity import auth, preferences, recovery, sessions
from app.modules.identity.errors import (
    CurrentPasswordInvalidError,
    InvalidCredentialsError,
    ReauthFailedError,
)
from app.modules.identity.schemas import (
    LoginRequest,
    PasswordChange,
    ReauthenticateRequest,
    ReauthenticateResponse,
    SessionUser,
    UserPreferences,
    UserPreferencesUpdate,
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
        **RATE_LIMIT_RESPONSES,
    },
)
async def login(
    limiter: Annotated[RateLimiter, rate_limit("login.ip", "login.username")],
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
    Too many attempts from one IP or for one username: ``429 rate_limited``
    with ``Retry-After``, whether or not the account exists.
    """
    # Before any lookup and before the password check, in its own committed
    # transaction (the request's Unit of Work has not used a connection yet).
    reservation = await limiter.reserve(
        auth.LOGIN_USERNAME_RATE_LIMIT, auth.login_rate_limit_key(body.username)
    )
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
    # A success gives its unit back, kept only if the login commits.
    await reservation.release(uow.session)
    # Max-Age = the session's absolute timeout (#25), never longer.
    lifetime = result.limits.absolute
    sessions.set_session_cookie(response, settings, result.token, max_age=lifetime)
    return await auth.session_user(
        uow.session, result.user, result.principal, result.token
    )


@router.post("/logout", status_code=204, responses={**AUTH_RESPONSES, **CSRF_RESPONSES})
async def logout(
    principal: Annotated[Principal, authenticated(notice_exempt=True)],
    request: Request,
    response: Response,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> None:
    """Sign out: revoke the current session and clear the cookie. Works
    before the privacy notice is accepted (#174).

    Needs the app's ``Origin`` and the session's ``X-CSRF-Token``.
    """
    await auth.logout(uow.session, request, settings, principal)
    sessions.clear_session_cookie(response, settings)


@router.get("/me", responses=AUTH_RESPONSES)
async def me(
    principal: Annotated[Principal, authenticated(notice_exempt=True)],
    request: Request,
    uow: UnitOfWorkDep,
    settings: SettingsDep,
) -> SessionUser:
    """The signed-in user, their roles, their effective permissions, whether
    they must accept the privacy notice first, and the session's CSRF token
    (the same value for the whole session). Works before the privacy notice
    is accepted (#174)."""
    return await auth.current_session_user(uow.session, request, settings, principal)


@router.patch(
    "/me/preferences",
    responses={
        **AUTH_RESPONSES,
        **CSRF_RESPONSES,
        422: problem_response_doc(
            "The body is not valid (validation_error): empty, an unknown value,"
            " null, or another field."
        ),
    },
)
async def update_preferences(
    principal: Annotated[Principal, authenticated(notice_exempt=True)],
    body: UserPreferencesUpdate,
    uow: UnitOfWorkDep,
) -> UserPreferences:
    """Store the signed-in user's UI language, theme or both (#36).

    Only the fields sent change; the last write wins (no ``version``). A
    stored value can't be cleared: ``null``, an empty body, an unknown value
    or another field is ``422``. Changes only the caller's own preferences.
    Works before the privacy notice is accepted (#174): the notice page has
    the language switcher. Needs the app's ``Origin`` and the session's
    ``X-CSRF-Token``. Returns both values after the save.
    """
    sent = body.model_fields_set
    return await preferences.update_user_preferences(
        uow.session,
        principal.user_id,
        language=body.language if "language" in sent else preferences.UNSET,
        theme=body.theme if "theme" in sent else preferences.UNSET,
    )


@router.post(
    "/reauthenticate",
    responses={
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The password is incorrect (reauth_failed), or a failed Origin or"
            " X-CSRF-Token check (csrf_failed)."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def reauthenticate(
    principal: Annotated[Principal, authenticated()],
    resolved: Annotated[ResolvedSession, Depends(get_current_session)],
    limiter: Annotated[RateLimiter, rate_limit(auth.REAUTH_RATE_LIMIT)],
    body: ReauthenticateRequest,
    uow: UnitOfWorkDep,
    sessionmaker: SessionmakerDep,
) -> ReauthenticateResponse:
    """Confirm the signed-in user's password for sensitive actions.

    On success the **current** session counts as reauthenticated for 5
    minutes from now (activity does not extend it; a later reauthentication
    restarts it). A wrong password is ``403 reauth_failed``; the session stays
    signed in and its window is unchanged. Needs the app's ``Origin`` and the
    session's ``X-CSRF-Token``. After 5 wrong passwords for the user in a
    15-minute window, further attempts get ``429 rate_limited`` with
    ``Retry-After``, without a password check; the session and its window are
    unchanged.
    """
    if resolved.session_id is None:  # pragma: no cover - the resolver sets it
        raise AuthenticationRequiredError()
    # End the transaction the session lookup opened, keeping its activity
    # update (#25), so the rate-limit counter's own committed transaction
    # never runs while this request holds a second connection.
    await uow.session.commit()
    result = await auth.reauthenticate(
        uow.session, principal, resolved.session_id, body.password, limiter
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


@router.post(
    "/password",
    responses={
        **AUTH_RESPONSES,
        403: problem_response_doc(
            "The current password is incorrect (current_password_invalid), or a"
            " failed Origin or X-CSRF-Token check (csrf_failed)."
        ),
        422: problem_response_doc(
            "The body is not valid (validation_error) or the new password"
            " breaks the policy (password_too_short, password_too_long,"
            " password_equals_username)."
        ),
        **RATE_LIMIT_RESPONSES,
    },
)
async def change_password(
    principal: Annotated[Principal, authenticated()],
    resolved: Annotated[ResolvedSession, Depends(get_current_session)],
    limiter: Annotated[RateLimiter, rate_limit(auth.REAUTH_RATE_LIMIT)],
    body: PasswordChange,
    response: Response,
    uow: UnitOfWorkDep,
    sessionmaker: SessionmakerDep,
    settings: SettingsDep,
) -> SessionUser:
    """Change the signed-in user's password, given the current one.

    On success every session of the user ends, including this one, and a new
    session replaces it: the response sets the new cookie and carries the new
    session's ``csrf_token`` (the old cookie and token stop working). Any
    outstanding reset link of the user stops working. A new password that
    breaks the policy is ``422`` and costs no attempt. A wrong current
    password is ``403 current_password_invalid``; the session stays signed in.
    Wrong current passwords count against the same budget as
    ``POST /reauthenticate``: after 5 in 15 minutes, ``429 rate_limited`` with
    ``Retry-After``, without a password check and without changing the
    session. Needs the app's ``Origin`` and the session's ``X-CSRF-Token``.
    """
    if resolved.session_id is None:  # pragma: no cover - the resolver sets it
        raise AuthenticationRequiredError()
    # The policy first: a mistake in the new password costs no attempt.
    await recovery.check_new_password(uow.session, principal, body.new_password)
    # End the transaction the session lookup opened, keeping its activity
    # update (#25), so the rate-limit counter's own committed transaction
    # never runs while this request holds a second connection.
    await uow.session.commit()
    result = await recovery.change_password(
        uow.session,
        principal,
        resolved.session_id,
        current_password=body.current_password,
        new_password=body.new_password,
        limiter=limiter,
    )
    if isinstance(result, recovery.ChangeFailure):
        # As for a failed reauthentication: end the request's transaction so
        # its connection goes back to the pool, then commit the event on its
        # own, because the 403 rolls the request's Unit of Work back.
        await uow.session.rollback()
        await recovery.record_failed_change(sessionmaker, result)
        raise CurrentPasswordInvalidError()
    # Max-Age = the new session's absolute timeout (#25), as on login.
    lifetime = result.limits.absolute
    sessions.set_session_cookie(response, settings, result.token, max_age=lifetime)
    return await auth.session_user(
        uow.session, result.user, result.principal, result.token
    )
