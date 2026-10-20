"""The administration module's public service (ADR-0002).

- The business settings (#37): :func:`get_business_settings`,
  :func:`update_business_settings`, the center time zone
  (:func:`get_center_time_zone`, :func:`center_today`) and the time-zone
  dependency registry (:func:`register_time_zone_dependency`); implemented in
  :mod:`app.modules.administration.settings`.
- The approval inbox core (#40): handlers and their registry
  (:class:`ApprovalHandler`, :func:`register_approval_handler`,
  :func:`registered_approval_types`), the account-reference resolver
  (:func:`register_account_reference_resolver`), creating and requester
  actions (:func:`create_approval_request`, :func:`cancel_approval_request`,
  :func:`resubmit_approval_request`), reviewer decisions and reads;
  implemented in :mod:`app.modules.administration.approvals`.
- Notifications (#41): types and their params models
  (:func:`register_notification_type`), :func:`create_notification` inside
  the caller's transaction, and the recipient's reads and read state;
  implemented in :mod:`app.modules.administration.notifications`.
- Reading the audit log, below.

**Audit log** (spec §9; architecture §4.4 cursor pagination).

Newest first, by ``(occurred_at DESC, id DESC)``. The cursor is the keyset
position ``[occurred_at, id]`` of the last item of a page; the next page
continues strictly after it, so events written meanwhile (always newer) never
shift later pages. Every holder of ``audit.view`` sees the whole log: there is
no record-level policy, and an unknown record or actor gives an empty page.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import AuditEvent
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    build_cursor_page,
    decode_cursor,
)
from app.modules.administration.approvals import (
    AccountReferenceResolver as AccountReferenceResolver,
)
from app.modules.administration.approvals import (
    AccountResolverMissingError as AccountResolverMissingError,
)
from app.modules.administration.approvals import (
    ApprovalHandler as ApprovalHandler,
)
from app.modules.administration.approvals import (
    ApprovalRequestFilters as ApprovalRequestFilters,
)
from app.modules.administration.approvals import (
    ApprovalRequestView as ApprovalRequestView,
)
from app.modules.administration.approvals import (
    cancel_approval_request as cancel_approval_request,
)
from app.modules.administration.approvals import (
    create_approval_request as create_approval_request,
)
from app.modules.administration.approvals import (
    decide_approval_request as decide_approval_request,
)
from app.modules.administration.approvals import (
    get_approval_request as get_approval_request,
)
from app.modules.administration.approvals import (
    list_approval_requests as list_approval_requests,
)
from app.modules.administration.approvals import (
    register_account_reference_resolver as register_account_reference_resolver,
)
from app.modules.administration.approvals import (
    register_approval_handler as register_approval_handler,
)
from app.modules.administration.approvals import (
    registered_account_reference_resolver as registered_account_reference_resolver,
)
from app.modules.administration.approvals import (
    registered_approval_types as registered_approval_types,
)
from app.modules.administration.approvals import (
    resubmit_approval_request as resubmit_approval_request,
)
from app.modules.administration.approvals import (
    unregister_approval_handler as unregister_approval_handler,
)
from app.modules.administration.notifications import (
    InvalidNotificationParamsError as InvalidNotificationParamsError,
)
from app.modules.administration.notifications import (
    create_notification as create_notification,
)
from app.modules.administration.notifications import (
    get_notification as get_notification,
)
from app.modules.administration.notifications import (
    list_notifications as list_notifications,
)
from app.modules.administration.notifications import (
    mark_all_notifications_read as mark_all_notifications_read,
)
from app.modules.administration.notifications import (
    mark_notification_read as mark_notification_read,
)
from app.modules.administration.notifications import (
    register_notification_type as register_notification_type,
)
from app.modules.administration.notifications import (
    registered_notification_types as registered_notification_types,
)
from app.modules.administration.notifications import (
    unread_notification_count as unread_notification_count,
)
from app.modules.administration.notifications import (
    unregister_notification_type as unregister_notification_type,
)
from app.modules.administration.notifications import (
    validated_params as validated_params,
)
from app.modules.administration.schemas import AuditChange, AuditEventListItem
from app.modules.administration.settings import (
    TimeZoneDependency as TimeZoneDependency,
)
from app.modules.administration.settings import (
    center_today as center_today,
)
from app.modules.administration.settings import (
    get_business_settings as get_business_settings,
)
from app.modules.administration.settings import (
    get_center_time_zone as get_center_time_zone,
)
from app.modules.administration.settings import (
    register_time_zone_dependency as register_time_zone_dependency,
)
from app.modules.administration.settings import (
    time_zone_dependencies as time_zone_dependencies,
)
from app.modules.administration.settings import (
    unregister_time_zone_dependency as unregister_time_zone_dependency,
)
from app.modules.administration.settings import (
    update_business_settings as update_business_settings,
)


@dataclass(frozen=True)
class AuditEventFilters:
    """Validated filters; ``record_type`` and ``record_id`` come together."""

    record_type: str | None = None
    record_id: uuid.UUID | None = None
    actor_id: uuid.UUID | None = None
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    occurred_at, event_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(occurred_at))
        identifier = uuid.UUID(str(event_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


def _filtered(filters: AuditEventFilters) -> Select[AuditEvent]:
    query = select(AuditEvent)
    if filters.record_type is not None:
        query = query.where(
            AuditEvent.record_type == filters.record_type,
            AuditEvent.record_id == filters.record_id,
        )
    if filters.actor_id is not None:
        query = query.where(AuditEvent.actor_user_id == filters.actor_id)
    if filters.occurred_from is not None:
        query = query.where(AuditEvent.occurred_at >= filters.occurred_from)
    if filters.occurred_to is not None:
        query = query.where(AuditEvent.occurred_at < filters.occurred_to)
    return query


def _item(event: AuditEvent) -> AuditEventListItem:
    return AuditEventListItem(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_id=event.actor_user_id,
        action=event.action,
        record_type=event.record_type,
        record_id=event.record_id,
        changes=(
            {name: AuditChange(**change) for name, change in event.changes.items()}
            if event.changes is not None
            else None
        ),
        details=event.details,
        request_id=event.request_id,
    )


async def list_audit_events(
    session: AsyncSession, filters: AuditEventFilters, page: CursorParams
) -> CursorPage[AuditEventListItem]:
    """One page of matching events, newest first."""
    query = _filtered(filters)
    if page.cursor is not None:
        when, identifier = _position(page.cursor)
        query = query.where(
            tuple_(AuditEvent.occurred_at, AuditEvent.id) < tuple_(when, identifier)
        )
    query = query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc()).limit(
        page.limit + 1
    )
    events = list((await session.scalars(query)).all())
    items = [_item(event) for event in events]
    return build_cursor_page(
        items,
        limit=page.limit,
        key=lambda item: [item.occurred_at.isoformat(), str(item.id)],
    )
