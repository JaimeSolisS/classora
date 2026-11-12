"""The general in-process API rate limit (#163; ADR-0008 D16 and its
2026-11-12 amendment).

A coarse limit on every API request, per API process and client IP, in
memory. No database, no Redis, no Caddy module. The authentication and
domain limits (``app.core.ratelimit``, PostgreSQL, ADR-0008 D15) are
separate and still apply to the requests this limit lets through.

**Setting.** ``API_RATE_LIMIT_PER_MINUTE`` (``Settings.api_rate_limit_per_minute``,
default 600, ``0`` turns it off). Only ``app.main.create_app`` installs the
middleware; ``build_app()`` installs it only when given a limit above 0, so
tests that build the app themselves are unaffected.

**Where.** :class:`ApiRateLimitMiddleware` is a pure ASGI middleware inside
``SecurityHeadersMiddleware`` and ``RequestContextMiddleware`` (a ``429``
gets the security headers, ``X-Request-ID`` and its access line) and outside
routing, authentication, the ``Origin`` check and the database. Only HTTP
requests are counted. ``GET /health/live`` and ``GET /health/ready`` (the
health checks, the deploy readiness gate and monitoring) are never counted.

**Windows.** Fixed 60-second windows aligned to the epoch minute, in UTC,
with the time from :func:`app.core.clock.now` (as D15). Only the current
window's counters are kept: the map is replaced when the window changes. A
burst of up to twice the limit across a boundary is the accepted cost.
Counting has no ``await`` between the read and the write, so it is atomic on
the event loop without a lock. At most :data:`MAX_TRACKED_CLIENTS` keys are
tracked per window; a new key beyond that is let through uncounted (fail
open) and one ``api_rate_limit.overflow`` warning is written per window.

**Key.** ``app.core.client_ip``'s :func:`~app.core.client_ip.client_ip` and
:func:`~app.core.client_ip.ip_key` (``TRUSTED_PROXIES``, IPv6 by ``/64``, a
peer that is not an IP address shares the ``unknown`` key).

**429.** The ``rate_limited`` Problem with the same fixed detail as
``app.core.ratelimit.RateLimitedError`` and ``Retry-After`` (whole seconds
until the window ends, rounded up, at least 1). The body never names the
policy, the key or the limit; the request body is never read and the app is
not called. One ``WARNING`` ``api_rate_limit.exceeded`` on ``app.security``
per client per window, with the method and the policy code only: never the
IP, the key or the raw path (no route has matched yet, and a raw path can
carry personal data; #617 QA D1).

**Per process.** Each API process has its own counters. Each AWS host runs
one uvicorn process (``infrastructure/docker/api.Dockerfile``), so the limit
is per host there.

Imports nothing from ``sqlalchemy``, ``app.modules`` or ``app.workflows``
(ADR-0002).
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core import clock
from app.core.client_ip import client_ip, ip_key
from app.core.config import Settings, get_settings
from app.core.errors import problem_response

POLICY_CODE: Final = "api.ip"
"""The policy code in the log lines (never in a response)."""
WINDOW_SECONDS: Final = 60
MAX_TRACKED_CLIENTS: Final = 10_000
EXEMPT_PATHS: Final = frozenset({"/health/live", "/health/ready"})
"""Never counted for ``GET``: the health probes."""
RATE_LIMITED_CODE: Final = "rate_limited"
RATE_LIMITED_DETAIL: Final = "Too many attempts. Try again later."
"""The same detail as ``app.core.ratelimit.RateLimitedError`` (a unit test
pins it); not imported from there, which reaches the database layer."""

security_logger = logging.getLogger("app.security")


@dataclass(frozen=True)
class Verdict:
    """The limiter's answer for one request."""

    allowed: bool
    retry_after: int = 0
    """Seconds until the window ends (refused requests only)."""
    first_refusal: bool = False
    """The client's first refused request in this window (log it once)."""


class InProcessRateLimiter:
    """Fixed-window request counters per key, current window only."""

    def __init__(
        self,
        limit: int,
        *,
        now: Callable[[], datetime] = clock.now,
        max_keys: int = MAX_TRACKED_CLIENTS,
    ) -> None:
        if limit < 1:
            raise ValueError("the limit must be at least 1")
        self.limit = limit
        self._now = now
        self._max_keys = max_keys
        self._window_start: int | None = None
        self._counts: dict[str, int] = {}
        self._refused: set[str] = set()
        self._overflow_logged = False

    @property
    def tracked_keys(self) -> frozenset[str]:
        """The keys counted in the current window."""
        return frozenset(self._counts)

    def hit(self, key: str) -> Verdict:
        """Take one unit for ``key``, or refuse when its budget is used up."""
        timestamp = self._now().timestamp()
        window_start = math.floor(timestamp / WINDOW_SECONDS) * WINDOW_SECONDS
        if window_start != self._window_start:
            self._window_start = window_start
            self._counts = {}
            self._refused = set()
            self._overflow_logged = False
        count = self._counts.get(key)
        if count is None:
            if len(self._counts) >= self._max_keys:
                if not self._overflow_logged:
                    self._overflow_logged = True
                    security_logger.warning(
                        "api_rate_limit.overflow",
                        extra={"policy": POLICY_CODE, "max_clients": self._max_keys},
                    )
                return Verdict(allowed=True)
            count = 0
        if count < self.limit:
            self._counts[key] = count + 1
            return Verdict(allowed=True)
        retry_after = max(1, math.ceil(window_start + WINDOW_SECONDS - timestamp))
        first = key not in self._refused
        self._refused.add(key)
        return Verdict(allowed=False, retry_after=retry_after, first_refusal=first)


def _settings_of(scope: Scope) -> Settings:
    """The app's settings, honouring ``dependency_overrides[get_settings]``
    (the test seam, as the lifespan does)."""
    app = scope.get("app")
    overrides = getattr(app, "dependency_overrides", None) or {}
    provider: Callable[[], Settings] = overrides.get(get_settings, get_settings)
    return provider()


class ApiRateLimitMiddleware:
    """Refuses a client's requests beyond ``limit_per_minute`` per window."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit_per_minute: int,
        now: Callable[[], datetime] = clock.now,
    ) -> None:
        self.app = app
        self.limiter = InProcessRateLimiter(limit_per_minute, now=now)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or (
            scope["method"] == "GET" and scope["path"] in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return
        key = ip_key(client_ip(Request(scope), _settings_of(scope)))
        verdict = self.limiter.hit(key)
        if verdict.allowed:
            await self.app(scope, receive, send)
            return
        if verdict.first_refusal:
            security_logger.warning(
                "api_rate_limit.exceeded",
                extra={
                    "code": RATE_LIMITED_CODE,
                    "policy": POLICY_CODE,
                    "method": scope["method"],
                },
            )
        response = problem_response(
            429,
            RATE_LIMITED_CODE,
            RATE_LIMITED_DETAIL,
            headers={"Retry-After": str(verdict.retry_after)},
        )
        await response(scope, receive, send)
