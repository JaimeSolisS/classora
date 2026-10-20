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
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.authz import AUTH_RESPONSES, CSRF_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.pagination import CursorPage, CursorPagination
from app.core.uow import UnitOfWorkDep
from app.modules.administration import notifications
from app.modules.administration.schemas import (
    NotificationItem,
    NotificationReadAllBody,
    NotificationReadAllResult,
    NotificationUnreadCount,
)

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
