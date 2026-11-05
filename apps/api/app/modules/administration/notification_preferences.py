"""In-app notification preferences (#119 M8-02; spec §9 "in-app notification
preferences", architecture §7.2 "per-user preferences").

**Catalogue** (C1): the registered types whose ``recipient_roles`` meet the
caller's roles (a user with several roles sees the union), by
``notification_type``, each ``{notification_type, mandatory, enabled}``. A
display filter only: enforcement is by ``(user, type)`` in
:func:`~app.modules.administration.notifications.create_notification`.
``dev.demo`` registers no roles, so it's in no catalogue. A mandatory type is
always ``enabled``.

**Storage** (B1): one ``notification_opt_outs`` row per type turned off; no
row means on (a new type is on for everyone). Set semantics: neither
audited nor versioned, no ``idempotent()``, no reauthentication (like
``PATCH /api/v1/auth/me/preferences``, #36). Not retroactive (E6): existing
notifications stay, and turning a type back on brings nothing back.

**Changes** (C2, C3): 1 to 50 items, all or none. After the body shape, the
checks run in this order over every item, and the first that fails answers
``422`` naming each offending item's index: ``notification_type_duplicate``
→ ``notification_type_unknown`` (not in the caller's catalogue) →
``notification_type_mandatory`` (``enabled: false`` on a mandatory type;
``enabled: true`` on one is a no-op). Then, under the caller's transaction
advisory lock (``pg_advisory_xact_lock(PREFERENCES_LOCK_NAMESPACE,
hashtext(user_id::text))``, so two concurrent ``PATCH`` es of one user run
one after the other and the last wins per type, never a deadlock), ``DELETE``
the rows of the types turned on and ``INSERT … ON CONFLICT DO NOTHING`` those
turned off.

Only the caller's rows: no user id is ever taken from the request (C4).
"""

import uuid
from typing import Final

from sqlalchemy import cast, delete, func, select
from sqlalchemy.dialects.postgresql import TEXT, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import Principal
from app.modules.administration.errors import (
    NotificationTypeDuplicateError,
    NotificationTypeMandatoryError,
    NotificationTypeUnknownError,
)
from app.modules.administration.models import NotificationOptOut
from app.modules.administration.notifications import (
    NotificationTypeInfo,
    notification_type_info,
    registered_notification_types,
)
from app.modules.administration.schemas import (
    NotificationPreferenceItem,
    NotificationPreferences,
    NotificationPreferencesBody,
)

PREFERENCES_LOCK_NAMESPACE: Final = 480_401
"""The first key of the per-user preferences lock ("administration
notification preferences", #119), distinct from the scheduling locks."""


def catalogue_types(roles: frozenset[str]) -> dict[str, NotificationTypeInfo]:
    """The registered types whose ``recipient_roles`` meet ``roles``."""
    found: dict[str, NotificationTypeInfo] = {}
    for code in sorted(registered_notification_types()):
        info = notification_type_info(code)
        if info is not None and info.recipient_roles & roles:
            found[code] = info
    return found


async def _opted_out_types(session: AsyncSession, user_id: uuid.UUID) -> set[str]:
    return set(
        await session.scalars(
            select(NotificationOptOut.notification_type).where(
                NotificationOptOut.user_id == user_id
            )
        )
    )


async def list_notification_preferences(
    session: AsyncSession, *, principal: Principal
) -> NotificationPreferences:
    """``GET /api/v1/notifications/preferences`` (C1): the caller's
    catalogue; at most one statement (their opt-out rows)."""
    catalogue = catalogue_types(principal.roles)
    off = await _opted_out_types(session, principal.user_id) if catalogue else set()
    return NotificationPreferences(
        items=[
            NotificationPreferenceItem(
                notification_type=code,
                mandatory=info.mandatory,
                enabled=info.mandatory or code not in off,
            )
            for code, info in catalogue.items()
        ]
    )


async def update_notification_preferences(
    session: AsyncSession, *, principal: Principal, body: NotificationPreferencesBody
) -> NotificationPreferences:
    """``PATCH /api/v1/notifications/preferences`` (C2, C3): the changes, all
    or none, then the caller's catalogue."""
    catalogue = catalogue_types(principal.roles)
    seen: set[str] = set()
    duplicates: list[int] = []
    for index, item in enumerate(body.items):
        if item.notification_type in seen:
            duplicates.append(index)
        seen.add(item.notification_type)
    if duplicates:
        raise NotificationTypeDuplicateError(duplicates)
    unknown = [
        index
        for index, item in enumerate(body.items)
        if item.notification_type not in catalogue
    ]
    if unknown:
        raise NotificationTypeUnknownError(unknown)
    mandatory = [
        index
        for index, item in enumerate(body.items)
        if not item.enabled and catalogue[item.notification_type].mandatory
    ]
    if mandatory:
        raise NotificationTypeMandatoryError(mandatory)

    turned_on = sorted(
        item.notification_type
        for item in body.items
        if item.enabled and not catalogue[item.notification_type].mandatory
    )
    turned_off = sorted(
        item.notification_type for item in body.items if not item.enabled
    )
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                PREFERENCES_LOCK_NAMESPACE,
                func.hashtext(cast(str(principal.user_id), TEXT)),
            )
        )
    )
    if turned_on:
        await session.execute(
            delete(NotificationOptOut)
            .where(
                NotificationOptOut.user_id == principal.user_id,
                NotificationOptOut.notification_type.in_(turned_on),
            )
            .execution_options(synchronize_session=False)
        )
    if turned_off:
        await session.execute(
            insert(NotificationOptOut)
            .values(
                [
                    {"user_id": principal.user_id, "notification_type": code}
                    for code in turned_off
                ]
            )
            .on_conflict_do_nothing(index_elements=["user_id", "notification_type"])
        )
    return await list_notification_preferences(session, principal=principal)
