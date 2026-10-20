"""The local/DEV notification producer (#41 D).

``POST /api/v1/dev/notifications`` creates one ``dev.demo`` notification for
the caller and commits: the only producer before real ones arrive (#44), for
local use and the E2E tests (#41, #42). ``app.main.build_app`` registers the
``dev.demo`` type and mounts the route together, only when ``APP_ENV`` is
``local`` or ``dev`` (like ``/api/v1/dev/demo-jobs``); the exported contract
never includes either.
"""

from typing import Annotated, Final

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.authz import AUTH_RESPONSES, CSRF_RESPONSES, Principal, authenticated
from app.core.errors import problem_response_doc
from app.core.uow import UnitOfWorkDep
from app.modules.administration import notifications
from app.modules.administration.schemas import NotificationItem

DEV_DEMO_TYPE: Final = "dev.demo"

Label = Annotated[str, Field(min_length=1, max_length=100)]


class DevDemoParams(BaseModel):
    """The params of ``dev.demo``: a label shown in the message."""

    model_config = ConfigDict(extra="forbid")

    label: Label


class DevNotificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label


def register_dev_notification_type() -> None:
    """Register ``dev.demo``; called only where the dev route is mounted."""
    notifications.register_notification_type(DEV_DEMO_TYPE, DevDemoParams)


router = APIRouter(prefix="/api/v1/dev", tags=["dev"])


@router.post(
    "/notifications",
    status_code=status.HTTP_201_CREATED,
    responses={
        **AUTH_RESPONSES,
        **CSRF_RESPONSES,
        422: problem_response_doc("The label is missing or too long."),
    },
)
async def create_dev_notification(
    recipient: Annotated[Principal, authenticated()],
    body: DevNotificationBody,
    uow: UnitOfWorkDep,
) -> NotificationItem:
    """Create a ``dev.demo`` notification for the caller (local/DEV only)."""
    notification_id = await notifications.create_notification(
        uow.session,
        recipient_user_id=recipient.user_id,
        notification_type=DEV_DEMO_TYPE,
        params=DevDemoParams(label=body.label),
    )
    return await notifications.get_notification(
        uow.session,
        recipient_user_id=recipient.user_id,
        notification_id=notification_id,
    )
