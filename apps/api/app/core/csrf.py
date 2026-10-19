"""CSRF protection: the ``Origin`` check and the synchronizer token (#24; ADR-0007 D9).

Every request whose method is in :data:`UNSAFE_METHODS` must pass two checks.
Both fail with the same ``403 csrf_failed`` (:class:`CsrfFailedError`), so the
body never says which one failed:

- **Origin** (every route, ``public()`` ones included):
  :func:`require_trusted_origin`, an app-level dependency that ``build_app``
  installs, so it runs before authentication. The ``Origin`` header must
  appear exactly once and equal ``Settings.app_origin`` (``APP_ORIGIN``). A
  missing, ``null``, duplicated or different value fails. ``GET``, ``HEAD``
  and ``OPTIONS`` are never checked.
- **Token** (every ``authenticated()``/``require()`` route): the session
  resolved by ``app.core.authz.get_current_principal`` must come with an
  ``X-CSRF-Token`` header, exactly once, equal to
  :func:`csrf_token_for` of the session token in the cookie
  (:func:`verify_csrf_token`). It runs after the session is resolved (so a
  missing session is still ``401``) and before the permission check.

The token is **derived**, not stored: ``base64url(HMAC-SHA256(key=session
token, msg=b"classora/csrf/v1"))`` without padding (43 characters). It lives
as long as the session, is the same in every tab and changes on every login.
The login response and ``GET /api/v1/auth/me`` return it in their JSON; it is
never in a cookie.

Failures are logged once on ``app.security`` at ``WARNING`` with a reason
(:data:`CsrfFailureReason`), the method and the path, never the ``Origin``
value, the token or the cookie. They are not audited (an anonymous flood must
not grow ``audit_events``).

Standard library only. See ``_docs/api.md`` ("CSRF and Origin") and
``_docs/security-headers.md``.
"""

import base64
import hashlib
import hmac
import logging
from typing import Final, Literal

from fastapi import Request

from app.core.config import Settings, get_settings
from app.core.errors import AppError

UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER: Final = "X-CSRF-Token"
CSRF_TOKEN_MAX_LENGTH: Final = 128
CSRF_TOKEN_LENGTH: Final = 43
"""Characters of the unpadded base64url encoding of a SHA-256 digest."""
_CSRF_MESSAGE: Final = b"classora/csrf/v1"

CsrfFailureReason = Literal[
    "origin_missing", "origin_mismatch", "token_missing", "token_invalid"
]

security_logger = logging.getLogger("app.security")


class CsrfFailedError(AppError):
    """The ``Origin`` or the CSRF token check failed (one code for both)."""

    status = 403
    code = "csrf_failed"
    detail = "The request could not be verified. Reload the page and try again."

    def __init__(self) -> None:
        super().__init__()


def csrf_token_for(session_token: bytes) -> str:
    """The CSRF token of the session whose raw token is ``session_token``."""
    digest = hmac.new(session_token, _CSRF_MESSAGE, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def is_unsafe(request: Request) -> bool:
    return request.method.upper() in UNSAFE_METHODS


def _fail(request: Request, reason: CsrfFailureReason) -> CsrfFailedError:
    security_logger.warning(
        "CSRF check failed",
        extra={"reason": reason, "method": request.method, "path": request.url.path},
    )
    return CsrfFailedError()


def _settings(request: Request) -> Settings:
    # Read only for unsafe methods (a GET never needs the settings), and
    # through app.dependency_overrides[get_settings] like every dependency.
    provider = request.app.dependency_overrides.get(get_settings, get_settings)
    settings: Settings = provider()
    return settings


async def require_trusted_origin(request: Request) -> None:
    """App-level dependency: an unsafe request's ``Origin`` is the app origin."""
    if not is_unsafe(request):
        return
    values = request.headers.getlist("origin")
    if not values:
        raise _fail(request, "origin_missing")
    if len(values) != 1 or values[0] != _settings(request).app_origin:
        raise _fail(request, "origin_mismatch")


def verify_csrf_token(request: Request, session_token: bytes) -> None:
    """For an unsafe request: ``X-CSRF-Token`` matches the session's token.

    Raises :class:`CsrfFailedError` otherwise. Safe methods pass unchecked.
    """
    if not is_unsafe(request):
        return
    values = request.headers.getlist(CSRF_HEADER)
    if not values or values == [""]:
        raise _fail(request, "token_missing")
    if len(values) != 1 or len(values[0]) > CSRF_TOKEN_MAX_LENGTH:
        raise _fail(request, "token_invalid")
    expected = csrf_token_for(session_token)
    if not hmac.compare_digest(values[0].encode("ascii", "replace"), expected.encode()):
        raise _fail(request, "token_invalid")
