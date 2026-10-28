"""API idempotency keys for financial ``POST`` routes (#88; ADR-0006 E6,
ISSUE-F4).

A route that posts a ledger row declares :func:`idempotent`. The client sends
an ``Idempotency-Key`` header, one per submission, and sends the same key
again when it retries. A retried request gets the first response back without
running again; two concurrent copies run exactly once; the same key with a
different request is refused. Keys live in ``idempotency_keys``
(``app.core.idempotency_models``).

**Header.** ``Idempotency-Key`` must appear exactly once (the CSRF rule):
missing or empty is ``400 idempotency_key_required``; repeated, or not 16-128
characters of ``[A-Za-z0-9_-]``, is ``400 idempotency_key_invalid``. The body
never echoes the value. The web client sends ``crypto.randomUUID()``
(``apps/web/src/lib/idempotency.ts``).

**Hash.** ``request_hash = SHA-256(method 0x00 concrete path 0x00 canonical
body)``. The canonical body is the JSON body re-serialized with sorted keys,
``(",", ":")`` separators and ``ensure_ascii=False``; an empty body hashes as
empty (a body that is not JSON is hashed as sent). The query string is
**not** part of the hash: a decorated route takes its input from the path and
the JSON body only.

**Claim.** When the dependency resolves (before the route body and before
any business lock), one statement in the request's Unit of Work claims the
key: ``INSERT ... ON CONFLICT (user_id, endpoint, key) DO UPDATE SET <a fresh
claim> WHERE expires_at <= now RETURNING id``, with ``expires_at = now +``
:data:`RETENTION` (``now`` from :func:`app.core.clock.now`). ``endpoint`` is
the method and the matched route's path template (``"POST
/api/v1/billing/payments"``), never the concrete path.

- A returned row: the key is claimed and the route runs.
- No row: a live row exists. It is read back (under READ COMMITTED this sees
  the committed winner): the same hash is a **replay**, another hash is ``422
  idempotency_key_reused``.
- A key past its ``expires_at`` that the hourly purge hasn't deleted yet
  counts as absent: the statement replaces it atomically (retention is
  exactly 7 days, whatever the purge timing).
- A concurrent duplicate blocks on the unique index until the first request
  commits (then it replays) or rolls back (then it claims the key and runs).

**Same transaction.** The claim, the business change and the stored response
commit together. The route ends with ``return await idem.respond(model,
status_code=201)``: that stores the status and the JSON body on the claimed
row and returns a response with exactly that body. Only ``2xx`` responses are
stored. Any failure (an :class:`~app.core.errors.AppError`, a body ``422``, a
version conflict, a crash, a failed commit) rolls the claim back, so a retry
with the same key runs again.

**Replay.** The stored status and body (``application/json``; no body for a
``204``) with one ``Idempotent-Replayed: true`` header; ``X-Request-ID`` is
the new request's. The route body doesn't run and the Unit of Work rolls
back, so a replay writes nothing. The body is the snapshot of the first
response, not the record's current state. Other response headers are not
stored (decorated routes return JSON only).

**Check order.** ``Origin`` ``403`` -> session ``401`` -> CSRF ``403`` ->
privacy notice ``403`` -> permission ``403`` -> reauthentication ``403`` (when
declared) -> rate limit ``429`` (when declared) -> ``400`` key -> claim /
replay / ``422 idempotency_key_reused`` -> body ``422`` -> route. A replay
therefore re-runs the access checks: a user who lost the permission gets
``403``.

**Declaring it on a route.**

- Declare the access rule (``require(...)`` or ``authenticated()``) first, then
  ``rate_limit(...)`` if the route has one (a user-keyed limit after the claim
  is refused: the request's transaction is open), then
  ``idem: Annotated[IdempotentRequest, idempotent()]``.
- Declare ``response_model=`` and ``status_code=`` on the decorator for the
  OpenAPI document, and end with ``return await idem.respond(model,
  status_code=...)`` (``None`` with ``204``).
- Pass :data:`IDEMPOTENCY_RESPONSES` (``400`` and ``422``) and add
  ``idempotency = true`` to the route's ``tests/authz/matrix.toml`` entry. The
  OpenAPI document lists the header as a required parameter
  (:func:`install_idempotency`).

**Programming errors** (``500 internal_error``, rollback): the route returned
without calling ``respond()``; ``idempotent()`` resolved with no actor
recorded by ``authenticated()``/``require()`` (so it can't run before the
access rule, nor on a ``public()`` route); ``idempotent()`` on ``GET``,
``HEAD`` or ``OPTIONS``; ``respond()`` called twice.

**Logging.** A replay writes one ``INFO`` line, and ``idempotency_key_reused``
one ``WARNING`` line, on ``app.security``, each with the endpoint, method and
path only: never the key, the hash, the request body or the stored response.
Nothing is audited (the business route audits its own change).

**Jobs** never use this table: they insert on natural keys with ``ON CONFLICT
DO NOTHING`` (ADR-0006 J4).

Imports nothing from ``app.modules`` or ``app.workflows`` (ADR-0002).
"""

import hashlib
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any, Final, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from pydantic import BaseModel
from sqlalchemy import Table, func, null, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.routing import Match

from app.core import clock
from app.core.audit import request_actor_id
from app.core.authz import mark_dependency, route_markers
from app.core.errors import AppError, problem_response_doc
from app.core.idempotency_models import IdempotencyKey
from app.core.uow import UnitOfWorkDep

security_logger = logging.getLogger("app.security")

IDEMPOTENCY_HEADER: Final = "Idempotency-Key"
REPLAYED_HEADER: Final = "Idempotent-Replayed"
KEY_MIN_LENGTH: Final = 16
KEY_MAX_LENGTH: Final = 128
KEY_PATTERN_TEXT: Final = r"^[A-Za-z0-9_-]{16,128}$"
KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{16,128}", re.ASCII)
"""Matched with ``fullmatch`` (so no trailing newline gets through)."""

RETENTION: Final = timedelta(days=7)
"""How long a key replays (a code constant, ADR-0006 E6)."""

SAFE_METHODS: Final = frozenset({"GET", "HEAD", "OPTIONS"})


# --- Errors ---------------------------------------------------------------------------


class IdempotencyKeyRequiredError(AppError):
    status = 400
    code = "idempotency_key_required"
    detail = "This request needs an Idempotency-Key header."

    def __init__(self) -> None:
        super().__init__()


class IdempotencyKeyInvalidError(AppError):
    status = 400
    code = "idempotency_key_invalid"
    detail = (
        "The Idempotency-Key header must be sent once, with 16 to 128 letters,"
        " digits, '_' or '-'."
    )

    def __init__(self) -> None:
        super().__init__()


class IdempotencyKeyReusedError(AppError):
    status = 422
    code = "idempotency_key_reused"
    detail = "This Idempotency-Key was already used for a different request."

    def __init__(self) -> None:
        super().__init__()


IDEMPOTENCY_RESPONSES: Final[dict[int | str, dict[str, Any]]] = {
    400: problem_response_doc(
        "The Idempotency-Key header is missing (idempotency_key_required) or"
        " malformed (idempotency_key_invalid)."
    ),
    422: problem_response_doc(
        "Validation error, or the Idempotency-Key was already used for a"
        " different request (idempotency_key_reused)."
    ),
}
"""``responses=`` for every route declared with :func:`idempotent`."""

HEADER_PARAMETER_DOC: Final[dict[str, Any]] = {
    "name": IDEMPOTENCY_HEADER,
    "in": "header",
    "required": True,
    "description": (
        "One key per submission, reused when the same submission is retried"
        " (ADR-0006). The web client sends crypto.randomUUID()."
    ),
    "schema": {
        "type": "string",
        "minLength": KEY_MIN_LENGTH,
        "maxLength": KEY_MAX_LENGTH,
        "pattern": KEY_PATTERN_TEXT,
        "title": IDEMPOTENCY_HEADER,
    },
}
"""The header as it appears in a decorated operation's ``parameters``."""


# --- The header and the hash ----------------------------------------------------------


def parse_key(values: Sequence[str]) -> str:
    """The one ``Idempotency-Key`` value of a request (its header values).

    Raises :class:`IdempotencyKeyRequiredError` (missing or empty) or
    :class:`IdempotencyKeyInvalidError` (repeated, or not the format).
    """
    if not values or list(values) == [""]:
        raise IdempotencyKeyRequiredError()
    if len(values) != 1 or KEY_PATTERN.fullmatch(values[0]) is None:
        raise IdempotencyKeyInvalidError()
    return values[0]


def canonical_body(body: bytes) -> bytes:
    """The JSON body re-serialized with sorted keys and no whitespace.

    Empty stays empty; a body that is not JSON is returned as sent.
    """
    if not body:
        return b""
    try:
        value = json.loads(body)
    except ValueError:
        return body
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def request_hash(method: str, path: str, body: bytes) -> bytes:
    """``SHA-256(method 0x00 path 0x00 canonical body)`` (32 bytes)."""
    digest = hashlib.sha256()
    digest.update(method.upper().encode())
    digest.update(b"\x00")
    digest.update(path.encode())
    digest.update(b"\x00")
    digest.update(canonical_body(body))
    return digest.digest()


# --- The route declaration ------------------------------------------------------------


@dataclass(frozen=True)
class IdempotencyRule:
    """The route marker of :func:`idempotent` (not an access rule)."""


def route_idempotency_rules(route: APIRoute | RouteContext) -> list[IdempotencyRule]:
    """The idempotency markers on ``route`` (at most one is expected)."""
    return route_markers(route, IdempotencyRule)


def route_endpoint(request: Request) -> str:
    """``"<METHOD> <path template>"`` of the request's matched route.

    ``request.scope["route"]`` is the route as defined (without an
    ``include_router`` prefix); its contexts carry the full template. When one
    route object is included more than once, the context that matches the
    request is used.
    """
    route = request.scope.get("route")
    if route is None:
        raise RuntimeError("idempotent() needs a matched route")
    contexts = [
        context
        for context in iter_route_contexts(request.app.routes)
        if context.original_route is route
    ]
    if len(contexts) > 1:
        contexts = [c for c in contexts if c.matches(request.scope)[0] == Match.FULL]
    template = contexts[0].path_format if contexts else None
    if template is None:
        template = getattr(route, "path_format", None)
    if not isinstance(template, str):
        raise RuntimeError("idempotent() could not find the route's path template")
    return " ".join((request.method, template))


class IdempotentReplay(Exception):  # noqa: N818 - control flow, not an error
    """Raised by the claim to answer with a stored response (and roll back).

    Rendered by the handler that :func:`install_idempotency` registers.
    """

    def __init__(self, status: int, body: Any) -> None:
        super().__init__("idempotent replay")
        self.status = status
        self.body = body


_KEYS: Final[Table] = IdempotencyKey.__table__  # type: ignore[assignment]

HandleState = Literal["new", "claimed", "responded", "replayed"]


class IdempotentRequest:
    """Yielded by :func:`idempotent`: the claimed key of this request.

    The route ends with ``return await idem.respond(model, status_code=...)``.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        user_id: uuid.UUID,
        endpoint: str,
        key: str,
        request_hash: bytes,
        method: str,
        path: str,
    ) -> None:
        self._session = session
        self.user_id = user_id
        self.endpoint = endpoint
        self._key = key
        self._hash = request_hash
        self._method = method
        self._path = path
        self._row_id: uuid.UUID | None = None
        self.state: HandleState = "new"

    def __repr__(self) -> str:  # never shows the key or the hash
        return f"IdempotentRequest(endpoint={self.endpoint!r}, state={self.state!r})"

    @property
    def responded(self) -> bool:
        return self.state == "responded"

    def _log_extra(self) -> dict[str, str]:
        return {"endpoint": self.endpoint, "method": self._method, "path": self._path}

    async def claim(self) -> None:
        """Claim the key, or raise :class:`IdempotentReplay` /
        :class:`IdempotencyKeyReusedError` (module docstring)."""
        if self.state != "new":
            raise RuntimeError("an idempotency key is claimed once")
        # Two rounds: a live row read back as gone (its user was deleted
        # between the two statements) is claimed again.
        for _ in range(2):
            now = clock.now()
            claim = (
                insert(_KEYS)
                .values(
                    user_id=self.user_id,
                    endpoint=self.endpoint,
                    key=self._key,
                    request_hash=self._hash,
                    response_status=null(),
                    response_body=null(),
                    expires_at=now + RETENTION,
                )
                .on_conflict_do_update(
                    constraint="uq_idempotency_keys_user_endpoint_key",
                    set_={
                        "request_hash": self._hash,
                        "response_status": null(),
                        "response_body": null(),
                        "expires_at": now + RETENTION,
                        "created_at": func.now(),
                        "updated_at": func.now(),
                    },
                    where=_KEYS.c.expires_at <= now,
                )
                .returning(_KEYS.c.id)
            )
            claimed = (await self._session.execute(claim)).first()
            if claimed is not None:
                self._row_id = claimed[0]
                self.state = "claimed"
                return
            stored = (
                await self._session.execute(
                    select(
                        _KEYS.c.request_hash,
                        _KEYS.c.response_status,
                        _KEYS.c.response_body,
                    ).where(
                        _KEYS.c.user_id == self.user_id,
                        _KEYS.c.endpoint == self.endpoint,
                        _KEYS.c.key == self._key,
                    )
                )
            ).first()
            if stored is None:
                continue
            if bytes(stored.request_hash) != self._hash:
                security_logger.warning(
                    "Idempotency key reused for a different request",
                    extra={"code": IdempotencyKeyReusedError.code, **self._log_extra()},
                )
                raise IdempotencyKeyReusedError()
            if stored.response_status is None:  # pragma: no cover - E1 rules it out
                raise RuntimeError("a committed idempotency key has no response")
            self.state = "replayed"
            security_logger.info("Idempotent request replayed", extra=self._log_extra())
            raise IdempotentReplay(int(stored.response_status), stored.response_body)
        raise RuntimeError("the idempotency key could not be claimed")

    async def respond(self, content: BaseModel | None, *, status_code: int) -> Response:
        """Store the response on the claimed key and return it.

        ``content`` is the route's response model (serialized once in JSON
        mode, by alias, as FastAPI serializes a ``response_model``), or
        ``None`` with ``204``. ``status_code`` must be ``2xx``.
        """
        if self.state != "claimed":
            raise RuntimeError(
                f"respond() needs a claimed idempotency key (state {self.state!r})"
            )
        if not isinstance(status_code, int) or not 200 <= status_code <= 299:
            raise ValueError(f"only 2xx responses are stored, not {status_code!r}")
        if status_code == 204:
            if content is not None:
                raise ValueError("a 204 response has no content")
            body: Any = None
        elif not isinstance(content, BaseModel):
            raise ValueError("respond() takes a Pydantic model (or None with 204)")
        else:
            body = content.model_dump(mode="json", by_alias=True)
        await self._session.execute(
            update(_KEYS)
            .where(_KEYS.c.id == self._row_id)
            .values(
                response_status=status_code,
                response_body=null() if body is None else body,
                updated_at=func.now(),
            )
        )
        self.state = "responded"
        return stored_response(status_code, body)


def stored_response(
    status: int, body: Any, headers: dict[str, str] | None = None
) -> Response:
    """The response for a stored status and body (no body for ``None``)."""
    if body is None:
        return Response(status_code=status, headers=headers)
    return JSONResponse(body, status_code=status, headers=headers)


@dataclass(frozen=True)
class _Keyed:
    user_id: uuid.UUID
    key: str


async def _idempotency_key(request: Request) -> _Keyed:
    """The guards and the header check, before any database work (E6)."""
    if request.method.upper() in SAFE_METHODS:
        raise RuntimeError(
            f"idempotent() is only for unsafe methods ({request.method})"
        )
    actor = request_actor_id(request)
    if actor is None:
        raise RuntimeError(
            "idempotent() needs the route's access rule (authenticated() or"
            " require()) resolved before it"
        )
    return _Keyed(actor, parse_key(request.headers.getlist(IDEMPOTENCY_HEADER)))


def idempotent() -> Any:
    """Make a financial ``POST`` safe to retry; yields an
    :class:`IdempotentRequest` (see the module docstring)."""

    async def idempotent_access(
        request: Request,
        keyed: Annotated[_Keyed, Depends(_idempotency_key)],
        uow: UnitOfWorkDep,
    ) -> AsyncIterator[IdempotentRequest]:
        handle = IdempotentRequest(
            uow.session,
            user_id=keyed.user_id,
            endpoint=route_endpoint(request),
            key=keyed.key,
            request_hash=request_hash(
                request.method, request.url.path, await request.body()
            ),
            method=request.method,
            path=request.url.path,
        )
        await handle.claim()
        yield handle
        if not handle.responded:
            # Raised before the Unit of Work exits, so the claim rolls back:
            # a committed key always has its response.
            raise RuntimeError("an idempotent route must end with idem.respond(...)")

    return Depends(
        mark_dependency(idempotent_access, IdempotencyRule()), scope="function"
    )


# --- App installation -----------------------------------------------------------------


async def _replay_handler(request: Request, exc: Exception) -> Response:
    # Type narrowing only: the handler is registered for this exception type.
    assert isinstance(exc, IdempotentReplay)  # noqa: S101
    return stored_response(exc.status, exc.body, {REPLAYED_HEADER: "true"})


def _document_headers(app: FastAPI, document: dict[str, Any]) -> None:
    paths = document.get("paths", {})
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        if not route_idempotency_rules(context):
            continue
        for method in sorted(context.methods or ()):
            operation = paths.get(context.path_format, {}).get(method.lower())
            if operation is None:
                continue
            parameters: list[dict[str, Any]] = operation.setdefault("parameters", [])
            if not any(
                p.get("name") == IDEMPOTENCY_HEADER and p.get("in") == "header"
                for p in parameters
            ):
                parameters.append(dict(HEADER_PARAMETER_DOC))


def install_idempotency(app: FastAPI) -> None:
    """Render replays, and list the ``Idempotency-Key`` header as a required
    parameter of every decorated operation in the OpenAPI document."""
    app.add_exception_handler(IdempotentReplay, _replay_handler)
    build = app.openapi

    def openapi() -> dict[str, Any]:
        document = build()
        _document_headers(app, document)
        return document

    app.openapi = openapi  # type: ignore[method-assign]
