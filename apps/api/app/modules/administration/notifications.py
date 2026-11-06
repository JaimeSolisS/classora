"""In-app notifications (#41; spec §1, §9; ISSUE-X1).

A notification goes to one recipient and is stored as ``{notification_type,
params}``: the client renders the message from the i18n key
``notifications.types.<type with "." replaced by "_">``, so a language
switch applies to old notifications too. Other modules reach all of this
through ``administration.service`` (ADR-0002).

**Types** are registered in code, each with a Pydantic model of its params
(:func:`register_notification_type`), usually where the module is wired
(``app.wiring``). The code is dotted (``staff.qualification_approved``).
Each type also says whether it is ``mandatory`` and which ``recipient_roles``
(role codes, plain strings) can receive it (#119 A1): the preferences
catalogue (:mod:`app.modules.administration.notification_preferences`) shows
a user the types of their roles, a display filter only.

**Preferences** (#119 D): a user may turn an optional type off (a
``notification_opt_outs`` row). :func:`create_notification` then adds nothing
for them, sends no ``NOTIFY`` and returns ``None``. Mandatory types skip the
lookup and are always created. Not retroactive: existing notifications stay.

**Creating** (:func:`create_notification`) happens inside the caller's
transaction: the row is added and flushed, never committed, so the
notification exists only if the caller's Unit of Work commits. In the same
transaction it issues ``pg_notify`` on :data:`NOTIFICATION_CHANNEL` with
``{"user_id", "notification_id"}`` only (#42; ISSUE-N1). PostgreSQL delivers
a ``NOTIFY`` only when its transaction commits (also through PgBouncer in
transaction mode), and never after a rollback; the live stream
(:mod:`app.modules.administration.live`) fans it out.

**Params rules** (:func:`validated_params`): after the type's model, a flat
object of strings (at most :data:`PARAM_STRING_MAX_LENGTH` characters),
integers, booleans or ``null``; at most :data:`PARAMS_MAX_BYTES` bytes as
JSON; no key matching the sensitive pattern of ``app.core.audit``. Values are
data (names, ``YYYY-MM-DD`` dates, codes), never translated text. A broken
rule, like an unknown type, is a programming error: ``ValueError``.

**Reading**: a user sees, counts and marks only their own notifications;
another user's id answers ``404 not_found`` exactly like a missing one.
"""

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from pydantic import BaseModel, ValidationError
from sqlalchemy import Select, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import sensitive_keys
from app.core.authz import RecordNotFoundError
from app.core.pagination import (
    CursorPage,
    CursorParams,
    InvalidCursorError,
    build_cursor_page,
    decode_cursor,
)
from app.modules.administration.models import (
    NOTIFICATION_TYPE_MAX_LENGTH,
    NOTIFICATION_TYPE_PATTERN,
    Notification,
    NotificationOptOut,
)
from app.modules.administration.schemas import NotificationItem

PARAM_STRING_MAX_LENGTH: Final = 200
PARAMS_MAX_BYTES: Final = 2048

NOTIFICATION_CHANNEL: Final = "notifications"
"""The ``LISTEN``/``NOTIFY`` channel of new notifications (#42)."""

_TYPE_RE: Final = re.compile(NOTIFICATION_TYPE_PATTERN)


@dataclass(frozen=True)
class NotificationTypeInfo:
    """A registered type's flags (#119 A2)."""

    mandatory: bool
    recipient_roles: frozenset[str]


@dataclass(frozen=True)
class _Registered:
    params_model: type[BaseModel]
    info: NotificationTypeInfo


_types: dict[str, _Registered] = {}


class InvalidNotificationParamsError(ValueError):
    """The params break the type's model or the params rules. Names the
    rule, never a value."""


# --- Registry -------------------------------------------------------------------


def register_notification_type(
    code: str,
    params_model: type[BaseModel],
    *,
    mandatory: bool = False,
    recipient_roles: frozenset[str] = frozenset(),
) -> None:
    """Register a notification type, the model of its params and its flags
    (#119 A1): ``mandatory`` types can't be turned off; ``recipient_roles``
    are the role codes that can receive it (the preferences catalogue's
    filter; empty: in no catalogue).

    Registering the same model and flags again changes nothing (every
    composition root and every app build may register); another model or
    other flags for a registered code, or a code that isn't dotted lowercase,
    raise ``ValueError``.
    """
    if len(code) > NOTIFICATION_TYPE_MAX_LENGTH or not _TYPE_RE.fullmatch(code):
        raise ValueError(f"invalid notification type code: {code!r}")
    if not (isinstance(params_model, type) and issubclass(params_model, BaseModel)):
        raise ValueError(f"{code}: the params model must be a Pydantic model")
    entry = _Registered(
        params_model,
        NotificationTypeInfo(
            mandatory=bool(mandatory), recipient_roles=frozenset(recipient_roles)
        ),
    )
    registered = _types.get(code)
    if registered is not None and (
        registered.params_model is not params_model or registered.info != entry.info
    ):
        raise ValueError(f"notification type already registered: {code}")
    _types[code] = entry


def unregister_notification_type(code: str) -> None:
    """Remove a type (tests only); an unknown code changes nothing."""
    _types.pop(code, None)


def registered_notification_types() -> frozenset[str]:
    return frozenset(_types)


def notification_params_model(code: str) -> type[BaseModel] | None:
    """A registered type's params model, or ``None`` for an unknown code
    (the i18n inventory keeps the app's own types, #130 B1)."""
    registered = _types.get(code)
    return None if registered is None else registered.params_model


def notification_type_info(code: str) -> NotificationTypeInfo | None:
    """A registered type's flags, or ``None`` for an unknown code (#119 A2)."""
    registered = _types.get(code)
    return None if registered is None else registered.info


# --- Params ---------------------------------------------------------------------


def _check_flat(data: Mapping[str, Any]) -> None:
    for key, value in data.items():
        if value is None or isinstance(value, bool | int):
            continue
        if isinstance(value, str):
            if len(value) > PARAM_STRING_MAX_LENGTH:
                raise InvalidNotificationParamsError(
                    f"param {key!r} is longer than {PARAM_STRING_MAX_LENGTH} characters"
                )
            continue
        raise InvalidNotificationParamsError(
            f"param {key!r} must be a string, an integer, a boolean or null"
        )


def validated_params(
    notification_type: str, params: BaseModel | Mapping[str, Any]
) -> dict[str, Any]:
    """The params as stored: validated by the type's model, then by the
    params rules. Raises ``ValueError`` for an unknown type or bad params."""
    registered = _types.get(notification_type)
    if registered is None:
        raise ValueError(f"unknown notification type: {notification_type!r}")
    model = registered.params_model
    raw = params.model_dump() if isinstance(params, BaseModel) else dict(params)
    try:
        data = model.model_validate(raw).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidNotificationParamsError(
            f"params don't match the model of {notification_type}"
        ) from exc
    if not isinstance(data, dict):  # pragma: no cover - a BaseModel dumps a dict
        raise InvalidNotificationParamsError("params must be an object")
    if sensitive_keys(data):
        raise InvalidNotificationParamsError("params hold a sensitive key")
    _check_flat(data)
    size = len(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode())
    if size > PARAMS_MAX_BYTES:
        raise InvalidNotificationParamsError(
            f"params are larger than {PARAMS_MAX_BYTES} bytes as JSON"
        )
    return data


# --- Creating -------------------------------------------------------------------


async def create_notification(
    session: AsyncSession,
    *,
    recipient_user_id: uuid.UUID,
    notification_type: str,
    params: BaseModel | Mapping[str, Any],
) -> uuid.UUID | None:
    """Add one notification for ``recipient_user_id`` (in any account state:
    it waits until they can sign in) and flush. Never commits: it exists only
    if the caller's transaction commits.

    The params are validated first (a bad call raises even when muted). For
    an optional type, one ``SELECT`` looks for the recipient's opt-out row:
    with one, nothing is added, no ``NOTIFY`` is sent and the result is
    ``None`` (#119 D1). A mandatory type skips the lookup. Callers that count
    notifications count only the non-``None`` results.
    """
    data = validated_params(notification_type, params)
    if not _types[notification_type].info.mandatory and await _opted_out(
        session, recipient_user_id, notification_type
    ):
        return None
    notification = Notification(
        recipient_user_id=recipient_user_id,
        notification_type=notification_type,
        params=data,
    )
    session.add(notification)
    await session.flush()
    await session.execute(
        select(
            func.pg_notify(
                NOTIFICATION_CHANNEL,
                notify_payload(recipient_user_id, notification.id),
            )
        )
    )
    return notification.id


async def _opted_out(
    session: AsyncSession, user_id: uuid.UUID, notification_type: str
) -> bool:
    found = await session.scalar(
        select(NotificationOptOut.id).where(
            NotificationOptOut.user_id == user_id,
            NotificationOptOut.notification_type == notification_type,
        )
    )
    return found is not None


def notify_payload(user_id: uuid.UUID, notification_id: uuid.UUID) -> str:
    """The ``NOTIFY`` payload: compact JSON with the two ids only (no
    content, far below PostgreSQL's 8000-byte limit)."""
    return json.dumps(
        {"user_id": str(user_id), "notification_id": str(notification_id)},
        separators=(",", ":"),
    )


# --- Reading and read state -------------------------------------------------------


def _item(notification: Notification) -> NotificationItem:
    return NotificationItem(
        id=notification.id,
        notification_type=notification.notification_type,
        params=notification.params,
        created_at=notification.created_at,
        read_at=notification.read_at,
    )


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    created_at, notification_id = decode_cursor(cursor, types=(str, str))
    try:
        when = datetime.fromisoformat(str(created_at))
        identifier = uuid.UUID(str(notification_id))
    except ValueError:
        raise InvalidCursorError() from None
    if when.tzinfo is None:
        raise InvalidCursorError()
    return when, identifier


def _own(recipient_user_id: uuid.UUID) -> Select[Notification]:
    return select(Notification).where(
        Notification.recipient_user_id == recipient_user_id
    )


async def list_notifications(
    session: AsyncSession,
    *,
    recipient_user_id: uuid.UUID,
    unread_only: bool,
    page: CursorParams,
) -> CursorPage[NotificationItem]:
    """One page of the recipient's notifications, newest first by
    ``(created_at, id)``; the cursor is the position of the page's last item."""
    query = _own(recipient_user_id)
    if unread_only:
        query = query.where(Notification.read_at.is_(None))
    if page.cursor is not None:
        when, identifier = _position(page.cursor)
        query = query.where(
            tuple_(Notification.created_at, Notification.id) < tuple_(when, identifier)
        )
    query = query.order_by(
        Notification.created_at.desc(), Notification.id.desc()
    ).limit(page.limit + 1)
    rows = list((await session.scalars(query)).all())
    return build_cursor_page(
        [_item(row) for row in rows],
        limit=page.limit,
        key=lambda item: [item.created_at.isoformat(), str(item.id)],
    )


async def notification_ids_after(
    session: AsyncSession,
    *,
    recipient_user_id: uuid.UUID,
    after_id: uuid.UUID,
    limit: int,
) -> list[uuid.UUID] | None:
    """The ids of the recipient's notifications created strictly after
    ``after_id``'s ``(created_at, id)`` position, oldest first (#42 C3).

    ``None`` when ``after_id`` is not one of the recipient's notifications
    (missing or another user's: the caller can't tell which) or when more
    than ``limit`` follow it.
    """
    position = (
        await session.execute(
            select(Notification.created_at, Notification.id).where(
                Notification.recipient_user_id == recipient_user_id,
                Notification.id == after_id,
            )
        )
    ).one_or_none()
    if position is None:
        return None
    ids = list(
        (
            await session.scalars(
                select(Notification.id)
                .where(
                    Notification.recipient_user_id == recipient_user_id,
                    tuple_(Notification.created_at, Notification.id)
                    > tuple_(position.created_at, position.id),
                )
                .order_by(Notification.created_at, Notification.id)
                .limit(limit + 1)
            )
        ).all()
    )
    return ids if len(ids) <= limit else None


async def unread_notification_count(
    session: AsyncSession, *, recipient_user_id: uuid.UUID
) -> int:
    """How many of the recipient's notifications are unread (one statement,
    on the partial unread index)."""
    count = await session.scalar(
        select(func.count())
        .select_from(Notification)
        .where(
            Notification.recipient_user_id == recipient_user_id,
            Notification.read_at.is_(None),
        )
    )
    return int(count or 0)


async def get_notification(
    session: AsyncSession, *, recipient_user_id: uuid.UUID, notification_id: uuid.UUID
) -> NotificationItem:
    """One of the recipient's notifications; ``404`` for a missing one or
    another user's."""
    notification = await session.scalar(
        _own(recipient_user_id).where(Notification.id == notification_id)
    )
    if notification is None:
        raise RecordNotFoundError()
    return _item(notification)


async def mark_notification_read(
    session: AsyncSession, *, recipient_user_id: uuid.UUID, notification_id: uuid.UUID
) -> NotificationItem:
    """Mark one of the recipient's notifications read. Idempotent: ``read_at``
    keeps its first value. ``404`` for a missing one or another user's."""
    notification = await session.scalar(
        _own(recipient_user_id)
        .where(Notification.id == notification_id)
        .with_for_update()
    )
    if notification is None:
        raise RecordNotFoundError()
    if notification.read_at is None:
        notification.read_at = clock.now()
        await session.flush()
    return _item(notification)


async def mark_all_notifications_read(
    session: AsyncSession,
    *,
    recipient_user_id: uuid.UUID,
    up_to: uuid.UUID | None,
) -> int:
    """Mark the recipient's unread notifications read: all of them, or only
    those at or before ``up_to``'s ``(created_at, id)`` position, so one that
    arrived after the user's last look stays unread. ``up_to`` must be one of
    the recipient's notifications (``404`` otherwise). Returns how many
    changed."""
    statement = update(Notification).where(
        Notification.recipient_user_id == recipient_user_id,
        Notification.read_at.is_(None),
    )
    if up_to is not None:
        position = (
            await session.execute(
                select(Notification.created_at, Notification.id).where(
                    Notification.recipient_user_id == recipient_user_id,
                    Notification.id == up_to,
                )
            )
        ).one_or_none()
        if position is None:
            raise RecordNotFoundError()
        statement = statement.where(
            tuple_(Notification.created_at, Notification.id)
            <= tuple_(position.created_at, position.id)
        )
    result = await session.execute(
        statement.values(read_at=clock.now()).execution_options(
            synchronize_session=False
        )
    )
    return int(result.rowcount)  # type: ignore[attr-defined]
