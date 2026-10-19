"""Rate limits for the authentication endpoints (#27; ADR-0008 D15).

The counters live in PostgreSQL (``rate_limit_counters``,
``app.core.ratelimit_models``), so a limit holds across every API process.
No Redis.

**Policies.** :data:`RATE_LIMIT_POLICIES` holds the five ADR-0008 limits.
They are code constants, not settings (ADR-0008 fixes them). A policy counts
either **requests** (every request, keyed by the client IP) or **failures**
(failed password checks, keyed by an account: the normalized username or the
user id).

**Fixed windows.** A window starts at a multiple of its length since the Unix
epoch, in UTC (``:00``, ``:15``, ``:30``, ``:45`` for 15 minutes; the full hour
for one hour). Every time comes from :func:`app.core.clock.now`, never the
database's ``now()``. A burst of up to twice a limit across a window boundary
is the accepted cost of fixed windows. A window simply ends: nothing is ever
locked permanently.

**Counting.** One unit is taken with one atomic ``INSERT ... ON CONFLICT DO
UPDATE ... WHERE count < limit RETURNING count``. No row returned means the
limit is used up: the answer is ``429`` and the row is not changed. The
statement runs in its **own short transaction** on the request engine
(through PgBouncer, ADR-0003), committed before the handler continues, so a
later error response (``401``, ``403``, ``422``, ``500``) never rolls the
count back.

- ``requests`` policies: :func:`rate_limit` takes one unit of each when the
  dependency runs, before the handler and before body validation.
- ``failures`` policies: the handler **reserves** one unit
  (:meth:`RateLimiter.reserve`) **before** it verifies the password; a ``429``
  means no verification at all. A failed verification keeps the reservation
  (it is the failure). A success calls :meth:`Reservation.release` inside the
  request's Unit of Work (``count - 1``, never below 0), so the release is kept
  only if the request commits. A success releases only its own reservation and
  does not reset the window. This keeps the limit exact under concurrent
  requests, and a request that crashes after reserving keeps the unit (fails
  safe).

**One connection at a time.** A counter statement takes its own pooled
connection. The request's Unit of Work must therefore have no open
transaction when it runs (end it first: roll back what must not be kept, or
commit what must), otherwise one request would hold two connections and a
burst could exhaust the PgBouncer pool (#23 QA). The limiter checks this and
raises a programming error.

**Declaring it on a route.** ``rate_limit(*codes)`` is a dependency (in
``dependencies=[...]`` or as an annotated parameter); it yields a
:class:`RateLimiter` for the route's ``failures`` policies. An unknown code
raises when the route module is built; reserving a code the route did not
declare, or a ``requests`` code, raises a programming error. The route passes
:data:`RATE_LIMIT_RESPONSES` and lists the codes in ``tests/authz/matrix.toml``
(``rate_limit = [...]``). Its callable carries a :class:`RateLimitRule` marker
(not an :class:`~app.core.authz.AccessRule`).

**429.** :class:`RateLimitedError`: ``429 rate_limited`` with a fixed detail
and a ``Retry-After`` header (whole seconds until the exhausted window ends,
rounded up, at least 1; the latest end when several limits are exhausted at
once). The body never names the policy, the key or the account. Each ``429``
writes one ``WARNING`` line on ``app.security`` with the policy code, method
and path, never an IP, username, user id or key hash, and no audit event.

**Client IP.** ``app.core.client_ip`` (``TRUSTED_PROXIES``); IPv6 clients are
keyed by ``/64``. Keys are stored only as ``SHA-256("<policy>\\x00<key>")``.

**Later issues.** A policy no route declares yet is listed in
:data:`PENDING_POLICIES` with the issue that applies it; that issue removes it
(the permission-matrix static check enforces both directions).

Imports nothing from ``app.modules`` or ``app.workflows`` (ADR-0002).
"""

import hashlib
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, Request
from fastapi.routing import APIRoute, RouteContext
from sqlalchemy import Table, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core import clock
from app.core.authz import mark_dependency, route_markers
from app.core.client_ip import client_ip, ip_key
from app.core.config import Settings, get_settings
from app.core.db import get_engine
from app.core.errors import AppError, problem_response_doc
from app.core.ratelimit_models import RateLimitCounter
from app.core.uow import UnitOfWork, UnitOfWorkDep

PolicyKey = Literal["ip", "account"]
PolicyCounts = Literal["requests", "failures"]

security_logger = logging.getLogger("app.security")

FIFTEEN_MINUTES: Final = timedelta(minutes=15)
ONE_HOUR: Final = timedelta(hours=1)
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RateLimitPolicy:
    """One ADR-0008 D15 limit."""

    code: str
    limit: int
    window: timedelta
    key: PolicyKey
    """``ip``: the client IP (``app.core.client_ip``); ``account``: a key the
    handler passes (the normalized username, or the user id)."""
    counts: PolicyCounts
    """``requests``: every request; ``failures``: failed password checks."""
    description: str
    applied_by: str
    """The issue(s) that apply it to their endpoint(s)."""


_POLICIES: Final = (
    RateLimitPolicy(
        "login.ip",
        50,
        FIFTEEN_MINUTES,
        "ip",
        "requests",
        "Login attempts per client IP (POST /api/v1/auth/login).",
        "#27",
    ),
    RateLimitPolicy(
        "login.username",
        10,
        FIFTEEN_MINUTES,
        "account",
        "failures",
        "Failed logins per normalized submitted username, existing or not.",
        "#27",
    ),
    RateLimitPolicy(
        "reauth.user",
        5,
        FIFTEEN_MINUTES,
        "account",
        "failures",
        "Wrong passwords per user: reauthentication (POST"
        " /api/v1/auth/reauthenticate) and the current-password check of the"
        " self-service password change share this budget.",
        "#27, #30",
    ),
    RateLimitPolicy(
        "token_redemption.ip",
        20,
        FIFTEEN_MINUTES,
        "ip",
        "requests",
        "Invitation and password-reset token redemption per client IP (one"
        " shared budget).",
        "#29, #30",
    ),
    RateLimitPolicy(
        "registration.ip",
        10,
        ONE_HOUR,
        "ip",
        "requests",
        "Self-registration requests per client IP.",
        "#169",
    ),
)

RATE_LIMIT_POLICIES: Final[Mapping[str, RateLimitPolicy]] = MappingProxyType(
    {policy.code: policy for policy in _POLICIES}
)
"""Every ADR-0008 D15 rate-limit policy, by code."""

PENDING_POLICIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "registration.ip": "#169",
    }
)
"""Policies no route declares yet, with the issue that applies them. That
issue removes its entry when it declares the policy on its route."""


# --- Windows, keys and the error ----------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One fixed window ``[start, end)``."""

    start: datetime
    end: datetime

    def retry_after(self, now: datetime) -> int:
        """Whole seconds until the window ends, rounded up, at least 1."""
        return max(1, math.ceil((self.end - now).total_seconds()))


def window_for(policy: RateLimitPolicy, now: datetime) -> Window:
    """The epoch-aligned window of ``policy`` that contains ``now``."""
    length = policy.window
    start = _EPOCH + ((now - _EPOCH) // length) * length
    return Window(start, start + length)


def key_hash(policy_code: str, key: str) -> bytes:
    """``SHA-256("<policy>\\x00<key>")``: what the table stores instead of the key."""
    return hashlib.sha256(f"{policy_code}\x00{key}".encode()).digest()


class RateLimitedError(AppError):
    """Too many attempts. Never names the policy, the key or the account."""

    status = 429
    code = "rate_limited"
    detail = "Too many attempts. Try again later."

    def __init__(self, retry_after: int) -> None:
        super().__init__()
        self.retry_after = max(1, int(retry_after))
        self.headers = {"Retry-After": str(self.retry_after)}


RATE_LIMIT_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    429: {
        **problem_response_doc(
            "Too many attempts (rate_limited). Retry after the number of seconds"
            " in Retry-After."
        ),
        "headers": {
            "Retry-After": {
                "description": "Seconds until the limit's window ends.",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    },
}
"""``responses=`` for every route declared with :func:`rate_limit`."""


# --- The counter store ----------------------------------------------------------------

_COUNTERS: Final[Table] = RateLimitCounter.__table__  # type: ignore[assignment]
_COUNT = _COUNTERS.c["count"]


@dataclass(frozen=True)
class _Unit:
    policy: RateLimitPolicy
    key_hash: bytes
    window: Window


async def _take(connection: AsyncConnection, unit: _Unit) -> bool:
    """Take one unit atomically; ``False`` when the limit is used up."""
    values = {
        "policy": unit.policy.code,
        "key_hash": unit.key_hash,
        "window_start": unit.window.start,
        "window_end": unit.window.end,
        "count": 1,
    }
    statement = (
        insert(_COUNTERS)
        .values(values)
        .on_conflict_do_update(
            index_elements=["policy", "key_hash", "window_start"],
            set_={"count": _COUNT + 1, "updated_at": func.now()},
            where=_COUNT < unit.policy.limit,
        )
        .returning(_COUNT)
    )
    result = await connection.execute(statement)
    return result.first() is not None


async def _take_all(engine: AsyncEngine, units: Sequence[_Unit]) -> list[_Unit]:
    """Take one unit of each in one committed transaction; the exhausted ones."""
    exhausted: list[_Unit] = []
    async with engine.begin() as connection:
        for unit in units:
            if not await _take(connection, unit):
                exhausted.append(unit)
    return exhausted


@dataclass
class Reservation:
    """A reserved unit of a ``failures`` policy (see the module docstring)."""

    policy: str
    key_hash: bytes = field(repr=False)
    window_start: datetime
    released: bool = False

    async def release(self, session: AsyncSession) -> None:
        """Give the unit back in ``session`` (the request's Unit of Work), so it
        is kept only if that transaction commits. At most once."""
        if self.released:
            return
        await session.execute(
            update(_COUNTERS)
            .where(
                _COUNTERS.c.policy == self.policy,
                _COUNTERS.c.key_hash == self.key_hash,
                _COUNTERS.c.window_start == self.window_start,
            )
            .values(count=func.greatest(_COUNT - 1, 0), updated_at=func.now())
        )
        self.released = True


# --- The route declaration ------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitRule:
    """The route marker of :func:`rate_limit` (not an access rule)."""

    codes: tuple[str, ...]


def route_rate_limit_rules(route: APIRoute | RouteContext) -> list[RateLimitRule]:
    """The rate-limit markers on ``route`` (at most one is expected)."""
    return route_markers(route, RateLimitRule)


class RateLimiter:
    """Yielded by :func:`rate_limit`: reserves the route's ``failures`` policies."""

    def __init__(
        self,
        codes: Sequence[str],
        *,
        engine: AsyncEngine | None,
        method: str,
        path: str,
        uow: UnitOfWork | None = None,
    ) -> None:
        self.codes = tuple(codes)
        self._engine = engine
        self._method = method
        self._path = path
        self._uow = uow

    def _ensure_no_request_transaction(self) -> None:
        if self._uow is not None and self._uow.session.in_transaction():
            raise RuntimeError(
                "rate limit counters need the request's transaction ended first"
                " (one connection per request at a time)"
            )

    async def _take_units(self, units: Sequence[_Unit]) -> None:
        if self._engine is None:  # pragma: no cover - set by the dependency
            raise RuntimeError("no database engine for the rate limiter")
        self._ensure_no_request_transaction()
        now = clock.now()
        exhausted = await _take_all(self._engine, units)
        if exhausted:
            for unit in exhausted:
                security_logger.warning(
                    "Rate limit exceeded",
                    extra={
                        "code": RateLimitedError.code,
                        "policy": unit.policy.code,
                        "method": self._method,
                        "path": self._path,
                    },
                )
            raise RateLimitedError(max(u.window.retry_after(now) for u in exhausted))

    async def consume_requests(self, ip: str) -> None:
        """Take one unit of every declared ``requests`` policy for ``ip``."""
        now = clock.now()
        units = [
            _Unit(policy, key_hash(policy.code, ip), window_for(policy, now))
            for policy in (RATE_LIMIT_POLICIES[c] for c in self.codes)
            if policy.counts == "requests"
        ]
        if units:
            await self._take_units(units)

    async def reserve(self, code: str, key: str) -> Reservation:
        """Reserve one unit of the declared ``failures`` policy ``code`` for
        ``key`` before verifying a password (``429`` when used up)."""
        policy = RATE_LIMIT_POLICIES.get(code)
        if code not in self.codes or policy is None:
            raise RuntimeError(f"rate limit {code!r} is not declared on this route")
        if policy.counts != "failures":
            raise RuntimeError(f"rate limit {code!r} counts requests, not failures")
        unit = _Unit(policy, key_hash(code, key), window_for(policy, clock.now()))
        await self._take_units([unit])
        return Reservation(code, unit.key_hash, unit.window.start)


def _checked_codes(codes: Sequence[str]) -> tuple[str, ...]:
    if not codes:
        raise ValueError("rate_limit() needs at least one policy code")
    if len(set(codes)) != len(codes):
        raise ValueError(f"duplicate rate-limit policy codes: {codes!r}")
    for code in codes:
        policy = RATE_LIMIT_POLICIES.get(code)
        if policy is None:
            raise ValueError(f"not a rate-limit policy: {code!r}")
        if policy.counts == "requests" and policy.key != "ip":
            raise ValueError(f"a requests policy must be keyed by IP: {code!r}")
    return tuple(codes)


def rate_limit(*codes: str) -> Any:
    """Apply the rate-limit policies ``codes`` to a route; yields a
    :class:`RateLimiter` (see the module docstring)."""
    checked = _checked_codes(codes)

    async def rate_limit_access(
        request: Request,
        uow: UnitOfWorkDep,
        engine: Annotated[AsyncEngine, Depends(get_engine)],
        settings: Annotated[Settings, Depends(get_settings)],
    ) -> RateLimiter:
        limiter = RateLimiter(
            checked,
            engine=engine,
            method=request.method,
            path=request.url.path,
            uow=uow,
        )
        await limiter.consume_requests(ip_key(client_ip(request, settings)))
        return limiter

    return Depends(mark_dependency(rate_limit_access, RateLimitRule(checked)))
