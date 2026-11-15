"""A test-only router for the idempotency-key infrastructure (#88 C1, E3).

Included on ``build_app()`` only by ``tests/integration/test_idempotency.py``
(through ``auth_client(..., routers=[...])``); never mounted in the real app,
so the exported contract does not change.

Every route requires ``user.view`` (Owner and Administrator; a tutor gets
``403 permission_denied``) and declares :func:`app.core.idempotency.idempotent`.
Each run writes one countable **effect**: an explicit
``test.idempotent_effect`` audit event whose details carry the body's
``label``.

- ``POST /items/{item_id}``: ``201`` with the item. With
  :attr:`Controls.hold` set, the first request to reach the body sets
  :attr:`Controls.entered` after writing its effect and keeps its
  transaction open until the test sets ``hold`` (the concurrency tests).
- ``POST /items/{item_id}/archive``: ``204``, no body.
- ``POST /conflicts/{item_id}``: like ``/items``, then raises
  :attr:`Controls.failure` (a ``409`` by default) while it is switched on.
- ``POST /others/{item_id}``: a second decorated endpoint (B9).
- ``POST /forgetful``: returns without ``idem.respond()`` (B7).
- ``POST /unguarded``: ``idempotent()`` with no access rule (B7).
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated

import anyio
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

from app.core.audit import record_audit_event
from app.core.authz import PERMISSION_RESPONSES, require
from app.core.errors import AppError
from app.core.idempotency import (
    IDEMPOTENCY_RESPONSES,
    IdempotentRequest,
    idempotent,
)
from app.core.money import Money
from app.core.uow import UnitOfWorkDep
from app.modules.identity.permissions import USER_VIEW

PREFIX = "/api/v1/test-idempotency"
EFFECT_ACTION = "test.idempotent_effect"
HOLD_TIMEOUT_SECONDS = 10.0


class ItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    amount: Money
    lines: list[str] = []


class ItemOut(BaseModel):
    item_id: uuid.UUID
    label: str
    amount: Money
    lines: list[str]
    effect_id: uuid.UUID


class SampleConflictError(AppError):
    status = 409
    code = "sample_conflict"
    detail = "The sample route failed after writing its effect."


@dataclass
class Controls:
    """Test-controlled behavior of the router (reset by each test)."""

    hold: threading.Event | None = None
    """Set by the test to release the request that is holding."""
    entered: threading.Event = field(default_factory=threading.Event)
    """Set by the holding request once its claim and effect are written."""
    failure: Callable[[], BaseException] | None = None
    """What ``/conflicts`` raises while it is switched on."""
    fail_once: bool = False
    """Switch ``failure`` off after the first request that raises it."""

    def reset(self) -> None:
        self.hold = None
        self.entered = threading.Event()
        self.failure = None
        self.fail_once = False


ACCESS = [require(USER_VIEW)]
RESPONSES = {**PERMISSION_RESPONSES, **IDEMPOTENCY_RESPONSES}


async def _effect(uow: UnitOfWorkDep, label: str) -> uuid.UUID:
    effect_id = uuid.uuid7()
    record_audit_event(
        uow.session,
        action=EFFECT_ACTION,
        details={"label": label, "effect_id": str(effect_id)},
    )
    await uow.session.flush()
    return effect_id


async def _maybe_hold(controls: Controls) -> None:
    hold, controls.hold = controls.hold, None
    if hold is None:
        return
    controls.entered.set()
    released = await anyio.to_thread.run_sync(hold.wait, HOLD_TIMEOUT_SECONDS)
    if not released:  # pragma: no cover - a hung test
        raise RuntimeError("the test never released the held request")


def build_idempotency_router(controls: Controls) -> APIRouter:
    router = APIRouter(prefix=PREFIX, responses=RESPONSES)

    @router.post(
        "/items/{item_id}",
        status_code=201,
        response_model=ItemOut,
        dependencies=ACCESS,
    )
    async def create_item(
        item_id: uuid.UUID,
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        effect_id = await _effect(uow, body.label)
        await _maybe_hold(controls)
        out = ItemOut(item_id=item_id, effect_id=effect_id, **body.model_dump())
        return await idem.respond(out, status_code=201)

    @router.post("/items/{item_id}/archive", status_code=204, dependencies=ACCESS)
    async def archive_item(
        item_id: uuid.UUID,
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        await _effect(uow, body.label)
        return await idem.respond(None, status_code=204)

    @router.post(
        "/conflicts/{item_id}",
        status_code=201,
        response_model=ItemOut,
        dependencies=ACCESS,
    )
    async def conflicting_item(
        item_id: uuid.UUID,
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        effect_id = await _effect(uow, body.label)
        await _maybe_hold(controls)
        if controls.failure is not None:
            failure = controls.failure
            if controls.fail_once:
                controls.failure = None
            raise failure()
        out = ItemOut(item_id=item_id, effect_id=effect_id, **body.model_dump())
        return await idem.respond(out, status_code=201)

    @router.post(
        "/others/{item_id}",
        status_code=201,
        response_model=ItemOut,
        dependencies=ACCESS,
    )
    async def other_item(
        item_id: uuid.UUID,
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        effect_id = await _effect(uow, body.label)
        out = ItemOut(item_id=item_id, effect_id=effect_id, **body.model_dump())
        return await idem.respond(out, status_code=201)

    @router.post("/forgetful", status_code=201, dependencies=ACCESS)
    async def forgetful(
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> dict[str, str]:
        await _effect(uow, body.label)
        return {"label": body.label}

    @router.post("/unguarded", status_code=201)
    async def unguarded(
        body: ItemIn,
        uow: UnitOfWorkDep,
        idem: Annotated[IdempotentRequest, idempotent()],
    ) -> Response:
        await _effect(uow, body.label)
        return await idem.respond(None, status_code=204)

    return router
