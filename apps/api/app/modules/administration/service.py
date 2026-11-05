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
  :func:`resubmit_approval_request`, :func:`approval_request_id_for_subject`,
  :func:`open_approval_request`),
  reviewer decisions and reads, assignment (#118:
  :func:`assign_approval_request`, :func:`list_assignee_options`, the
  review-candidate resolver :func:`register_review_candidate_resolver` and
  :func:`register_approval_notifications`), and :class:`ApprovalTransitionInvalidError`
  for a handler that finds its subject already decided (#44);
  implemented in :mod:`app.modules.administration.approvals`.
- Notifications (#41): types and their params models
  (:func:`register_notification_type`), :func:`create_notification` inside
  the caller's transaction, and the recipient's reads and read state;
  implemented in :mod:`app.modules.administration.notifications`. #119: each
  type's flags (``mandatory``, ``recipient_roles``;
  :func:`notification_type_info`) and the caller's preferences
  (:func:`list_notification_preferences`,
  :func:`update_notification_preferences`, in
  :mod:`app.modules.administration.notification_preferences`);
  :func:`create_notification` returns ``None`` for a muted optional type.
- The scheduling policy (#61): :func:`get_scheduling_policy` (the version
  in force at an instant), :func:`get_scheduling_policy_version` (one
  version by id, #62) and :func:`get_policy_version_and_approval_id` (a
  version and an approval request id in one statement, #62 C1) and the pure evaluator
  (:class:`SchedulingPolicyValues`, :class:`PolicyOutcome`,
  :func:`classify_change`, :func:`violates_booking_notice`,
  :func:`default_respond_by`); implemented in
  :mod:`app.modules.administration.policy_versions` and
  :mod:`app.modules.administration.scheduling_policy`. Consumers import these
  names from here, never the model.
- The import pipeline's kinds registry (#122 A1, A2): other modules
  implement :class:`ImportKind` (with :class:`ImportColumn`,
  :class:`RowResult`, :class:`RowIssue`, :class:`ImportRowRef` and
  :class:`CommitOutcome`) and the composition roots register it with
  :func:`register_import_kind`; implemented in
  :mod:`app.modules.administration.imports`.
- Reading the audit log, below.

**Audit log** (spec §9; architecture §4.4 cursor pagination).

Newest first, by ``(occurred_at DESC, id DESC)``. The cursor is the keyset
position ``[occurred_at, id]`` of the last item of a page; the next page
continues strictly after it, so events written meanwhile (always newer) never
shift later pages. Every holder of ``audit.view`` sees the whole log: there is
no record-level policy, and an unknown record or actor gives an empty page.

#120 B1-B3: each item names its actor and the users its user-reference
columns point to (``app.core.audit.user_reference_columns``: never a masked
or excluded column), plus the record itself for ``users`` events, through
the registered account-reference resolver in one call per page (a missing
resolver fails closed). Masked values are never resolved, and ``details``
are not read. :func:`list_audit_record_types` lists the audited tables.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import (
    AuditEvent,
    audited_table_names,
    user_reference_columns,
)
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
    ReviewCandidateResolver as ReviewCandidateResolver,
)
from app.modules.administration.approvals import (
    ReviewCandidateResolverMissingError as ReviewCandidateResolverMissingError,
)
from app.modules.administration.approvals import (
    _account_names,
)
from app.modules.administration.approvals import (
    approval_request_id_for_subject as approval_request_id_for_subject,
)
from app.modules.administration.approvals import (
    assign_approval_request as assign_approval_request,
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
    list_assignee_options as list_assignee_options,
)
from app.modules.administration.approvals import (
    open_approval_request as open_approval_request,
)
from app.modules.administration.approvals import (
    register_account_reference_resolver as register_account_reference_resolver,
)
from app.modules.administration.approvals import (
    register_approval_handler as register_approval_handler,
)
from app.modules.administration.approvals import (
    register_approval_notifications as register_approval_notifications,
)
from app.modules.administration.approvals import (
    register_review_candidate_resolver as register_review_candidate_resolver,
)
from app.modules.administration.approvals import (
    registered_account_reference_resolver as registered_account_reference_resolver,
)
from app.modules.administration.approvals import (
    registered_approval_types as registered_approval_types,
)
from app.modules.administration.approvals import (
    registered_review_candidate_resolver as registered_review_candidate_resolver,
)
from app.modules.administration.approvals import (
    resubmit_approval_request as resubmit_approval_request,
)
from app.modules.administration.approvals import (
    unregister_approval_handler as unregister_approval_handler,
)
from app.modules.administration.errors import (
    ApprovalTransitionInvalidError as ApprovalTransitionInvalidError,
)
from app.modules.administration.imports.registry import (
    DEFAULT_BATCH_SIZE as DEFAULT_BATCH_SIZE,
)
from app.modules.administration.imports.registry import (
    CommitOutcome as CommitOutcome,
)
from app.modules.administration.imports.registry import (
    ImportColumn as ImportColumn,
)
from app.modules.administration.imports.registry import (
    ImportKind as ImportKind,
)
from app.modules.administration.imports.registry import (
    ImportRowRef as ImportRowRef,
)
from app.modules.administration.imports.registry import (
    RowIssue as RowIssue,
)
from app.modules.administration.imports.registry import (
    RowResult as RowResult,
)
from app.modules.administration.imports.registry import (
    register_import_kind as register_import_kind,
)
from app.modules.administration.imports.registry import (
    registered_import_kinds as registered_import_kinds,
)
from app.modules.administration.imports.registry import (
    run_settings as run_settings,
)
from app.modules.administration.notification_preferences import (
    list_notification_preferences as list_notification_preferences,
)
from app.modules.administration.notification_preferences import (
    update_notification_preferences as update_notification_preferences,
)
from app.modules.administration.notifications import (
    InvalidNotificationParamsError as InvalidNotificationParamsError,
)
from app.modules.administration.notifications import (
    NotificationTypeInfo as NotificationTypeInfo,
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
    notification_type_info as notification_type_info,
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
from app.modules.administration.policy_versions import (
    create_scheduling_policy as create_scheduling_policy,
)
from app.modules.administration.policy_versions import (
    delete_scheduling_policy as delete_scheduling_policy,
)
from app.modules.administration.policy_versions import (
    get_policy_version_and_approval_id as get_policy_version_and_approval_id,
)
from app.modules.administration.policy_versions import (
    get_scheduling_policy as get_scheduling_policy,
)
from app.modules.administration.policy_versions import (
    get_scheduling_policy_overview as get_scheduling_policy_overview,
)
from app.modules.administration.policy_versions import (
    get_scheduling_policy_version as get_scheduling_policy_version,
)
from app.modules.administration.scheduling_policy import (
    ChangeInitiator as ChangeInitiator,
)
from app.modules.administration.scheduling_policy import (
    ChangeKind as ChangeKind,
)
from app.modules.administration.scheduling_policy import (
    Classification as Classification,
)
from app.modules.administration.scheduling_policy import (
    PolicyOutcome as PolicyOutcome,
)
from app.modules.administration.scheduling_policy import (
    SchedulingPolicyValues as SchedulingPolicyValues,
)
from app.modules.administration.scheduling_policy import (
    classify_change as classify_change,
)
from app.modules.administration.scheduling_policy import (
    default_respond_by as default_respond_by,
)
from app.modules.administration.scheduling_policy import (
    violates_booking_notice as violates_booking_notice,
)
from app.modules.administration.schemas import (
    AccountName,
    AuditChange,
    AuditEventListItem,
)
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


USERS_TABLE = "users"


def _user_id(value: Any) -> uuid.UUID | None:
    """``value`` as a user id when it is a UUID string, else ``None``."""
    if not isinstance(value, str):
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _referenced_users(event: AuditEvent, columns: Iterable[str]) -> set[uuid.UUID]:
    """The users ``event`` names: the ``old``/``new`` values of its
    user-reference ``columns``, and the record of a ``users`` event."""
    found: set[uuid.UUID] = set()
    if event.record_type == USERS_TABLE and event.record_id is not None:
        found.add(event.record_id)
    for column in columns:
        change = (event.changes or {}).get(column)
        if not isinstance(change, dict):
            continue
        for side in ("old", "new"):
            user_id = _user_id(change.get(side))
            if user_id is not None:
                found.add(user_id)
    return found


def _item(
    event: AuditEvent,
    referenced: set[uuid.UUID],
    names: dict[uuid.UUID, AccountName],
) -> AuditEventListItem:
    actor = event.actor_user_id
    return AuditEventListItem(
        id=event.id,
        occurred_at=event.occurred_at,
        actor_id=actor,
        actor=names.get(actor) if actor is not None else None,
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
        accounts=sorted(
            (names[user_id] for user_id in referenced if user_id in names),
            key=lambda account: account.id,
        ),
    )


async def list_audit_events(
    session: AsyncSession, filters: AuditEventFilters, page: CursorParams
) -> CursorPage[AuditEventListItem]:
    """One page of matching events, newest first, with names (#120 B3: one
    event ``SELECT`` and at most one resolver statement)."""
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
    columns: dict[str | None, frozenset[str]] = {None: frozenset()}
    for event in events:
        table = event.record_type
        if table is not None and table not in columns:
            columns[table] = user_reference_columns(table)
    referenced = [
        _referenced_users(event, columns[event.record_type]) for event in events
    ]
    names = await _account_names(
        session,
        [
            *(event.actor_user_id for event in events),
            *(user_id for ids in referenced for user_id in ids),
        ],
    )
    items = [
        _item(event, ids, names) for event, ids in zip(events, referenced, strict=True)
    ]
    return build_cursor_page(
        items,
        limit=page.limit,
        key=lambda item: [item.occurred_at.isoformat(), str(item.id)],
    )


def list_audit_record_types() -> list[str]:
    """The audited table names, sorted (#120 B4): every mapped ``Audited``
    model the process imported (all of them in the app)."""
    return sorted(audited_table_names())
