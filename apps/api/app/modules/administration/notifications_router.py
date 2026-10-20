"""Notification routes (#41; ``app.modules.administration.notifications``).

Every route is ``authenticated()`` and works on the caller's own
notifications only: another user's id answers ``404 not_found`` exactly like
a missing one.

- ``GET /api/v1/notifications``: newest first, cursor pagination,
  ``unread_only``.
- ``GET /api/v1/notifications/unread-count``.
- ``POST /api/v1/notifications/{notification_id}/read``: idempotent.
- ``POST /api/v1/notifications/read-all`` (``{up_to}``): the unread ones at
  or before ``up_to`` (all of them when it's null).
- ``GET /api/v1/notifications/stream``: the caller's live notifications as
  Server-Sent Events (#42; :mod:`app.modules.administration.live`). The
  request never refreshes the session's ``last_seen_at``
  (:func:`~app.core.authz.passive_session`), and neither does the stream.
"""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request

from app.core.authz import (
    AUTH_RESPONSES,
    CSRF_RESPONSES,
    Principal,
    ResolvedSession,
    authenticated,
    get_current_session,
    passive_session,
)
from app.core.db import get_sessionmaker
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage, CursorPagination
from app.core.sse import EVENT_STREAM, EventStreamResponse
from app.core.uow import UnitOfWork, UnitOfWorkDep
from app.modules.administration import live, notifications
from app.modules.administration.errors import STREAM_RETRY_AFTER_SECONDS
from app.modules.administration.schemas import (
    NotificationItem,
    NotificationReadAllBody,
    NotificationReadAllResult,
    NotificationUnreadCount,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

Recipient = Annotated[Principal, authenticated()]

NOTICE = problem_response_doc(
    "The privacy notice must be accepted first (privacy_notice_required)."
)
NOT_FOUND = problem_response_doc("No such notification, or another user's (not_found).")
READ_RESPONSES: dict[int | str, dict[str, Any]] = {**AUTH_RESPONSES, 403: NOTICE}
WRITE_RESPONSES: dict[int | str, dict[str, Any]] = {
    **AUTH_RESPONSES,
    **CSRF_RESPONSES,
    404: NOT_FOUND,
}


@router.get(
    "",
    responses={
        **READ_RESPONSES,
        400: problem_response_doc("The pagination cursor is not valid."),
    },
)
async def list_notifications(
    recipient: Recipient,
    page: CursorPagination,
    uow: UnitOfWorkDep,
    unread_only: Annotated[
        bool, Query(description="Only the unread notifications.")
    ] = False,
) -> CursorPage[NotificationItem]:
    """The caller's notifications, newest first."""
    return await notifications.list_notifications(
        uow.session,
        recipient_user_id=recipient.user_id,
        unread_only=unread_only,
        page=page,
    )


@router.get("/unread-count", responses=READ_RESPONSES)
async def unread_count(
    recipient: Recipient, uow: UnitOfWorkDep
) -> NotificationUnreadCount:
    """How many of the caller's notifications are unread."""
    return NotificationUnreadCount(
        count=await notifications.unread_notification_count(
            uow.session, recipient_user_id=recipient.user_id
        )
    )


STREAM_RESPONSES: dict[int | str, dict[str, Any]] = {
    200: {
        "description": (
            "Server-Sent Events: `notification` (id = data.notification_id),"
            " `resync` and a `: heartbeat` comment every 15 s."
        ),
        "content": {EVENT_STREAM: {"schema": {"type": "string"}}},
    },
    **READ_RESPONSES,
    429: {
        **problem_response_doc(
            "The caller already has 10 open streams in this API process"
            " (too_many_streams)."
        ),
        "headers": {
            "Retry-After": {
                "description": f"Seconds to wait ({STREAM_RETRY_AFTER_SECONDS}).",
                "schema": {"type": "integer", "minimum": 1},
            }
        },
    },
}


@router.get(
    "/stream",
    # Before the access rule's resolver runs: this request is no activity.
    dependencies=[Depends(passive_session)],
    response_class=EventStreamResponse,
    status_code=200,
    responses=STREAM_RESPONSES,
)
async def stream(
    request: Request,
    recipient: Recipient,
    resolved: Annotated[ResolvedSession, Depends(get_current_session)],
    uow: UnitOfWorkDep,
    last_event_id: Annotated[
        str | None,
        Header(
            alias="Last-Event-ID",
            description="The id of the last notification event received"
            " (sent by the browser when it reconnects).",
        ),
    ] = None,
) -> EventStreamResponse:
    """The caller's new notifications, live (Server-Sent Events).

    Events carry only the notification id; the client refetches through the
    other routes. With ``Last-Event-ID`` the caller's later notifications
    (up to 100) come first, oldest first; more, or an id that isn't one of
    the caller's, gives one ``resync`` instead.
    """
    hub = live.notification_hub(request)
    subscriber = hub.subscribe(recipient.user_id)
    try:
        replay: list[uuid.UUID] | None = []
        if last_event_id is not None:
            replay = await _replay(uow, recipient.user_id, last_event_id)
    except BaseException:
        hub.unsubscribe(subscriber)
        raise
    check = live.registered_stream_session_check(request)
    sessionmaker = get_sessionmaker(request)
    session_id = resolved.session_id

    async def session_alive() -> bool:
        if check is None or session_id is None:
            return False  # fails closed
        try:
            async with UnitOfWork(sessionmaker) as check_uow:
                return await check(check_uow.session, session_id)
        except Exception as exc:
            logger.warning(
                "notification stream session check failed (%s)", type(exc).__name__
            )
            return False

    return EventStreamResponse(
        live.stream_events(
            hub=hub, subscriber=subscriber, replay=replay, session_alive=session_alive
        ),
        on_close=lambda: hub.unsubscribe(subscriber),
    )


async def _replay(
    uow: UnitOfWork, recipient_user_id: uuid.UUID, last_event_id: str
) -> list[uuid.UUID] | None:
    try:
        after_id = uuid.UUID(last_event_id.strip())
    except ValueError:
        return None
    return await notifications.notification_ids_after(
        uow.session,
        recipient_user_id=recipient_user_id,
        after_id=after_id,
        limit=live.REPLAY_LIMIT,
    )


@router.post("/read-all", responses=WRITE_RESPONSES)
async def read_all(
    recipient: Recipient,
    uow: UnitOfWorkDep,
    body: NotificationReadAllBody | None = None,
) -> NotificationReadAllResult:
    """Mark the caller's unread notifications read, up to ``up_to`` (the
    newest one the caller has seen) when it's given, so one that arrived
    meanwhile stays unread."""
    return NotificationReadAllResult(
        updated=await notifications.mark_all_notifications_read(
            uow.session,
            recipient_user_id=recipient.user_id,
            up_to=body.up_to if body is not None else None,
        )
    )


@router.post("/{notification_id}/read", responses=WRITE_RESPONSES)
async def read_one(
    recipient: Recipient, notification_id: uuid.UUID, uow: UnitOfWorkDep
) -> NotificationItem:
    """Mark one of the caller's notifications read (again: no change)."""
    return await notifications.mark_notification_read(
        uow.session,
        recipient_user_id=recipient.user_id,
        notification_id=notification_id,
    )
