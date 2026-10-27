"""Audit events (spec §9; architecture §4.3 step 6, §7.5, ISSUE-A3 accepted on #22).

Every audit event is one append-only row of ``audit_events``, written **in the
same transaction** as the change it records: if the business transaction rolls
back, its audit rows roll back with it, and if writing an audit row fails, the
business change fails too.

**Automatic events.** Mark a model with the :class:`Audited` mixin. A flush
hook (registered once on SQLAlchemy's ``Session`` class, so it covers every
session: the request Unit of Work, jobs, the CLI, seeds, factories) writes one
event per inserted, changed or deleted row:

- ``create``: every included column, ``{"old": null, "new": <value>}``;
- ``update``: only the changed included columns (a no-op change writes
  nothing);
- ``delete``: every included column, ``{"old": <value>, "new": null}``;
- ``soft_delete`` / ``restore``: a :class:`~app.core.models.SoftDeleteMixin`
  model whose ``deleted_at`` goes from null to a value, or back.

``id``, ``created_at``, ``updated_at`` and ``version`` are always left out.
``__audit_exclude__`` columns never appear, not even by name (password hashes,
tokens, file contents); ``__audit_mask__`` columns appear as ``"[masked]"``.

**Explicit events.** :func:`record_audit_event` writes a named event that is not
a column change (``identity.owner_bootstrapped``, login attempts, token
redemption, overrides with a reason).

**Actor and request ID.** ``request_id`` is the current request's ID
(``app.core.logging.get_request_id``), ``NULL`` outside a request. The actor
is read from the session when the event is written: in a request it is the
principal recorded by the ``authenticated()`` / ``require()`` access
declarations (:func:`record_request_actor`), so the order of the route's
dependencies does not matter; jobs and commands pass
``UnitOfWork(sessionmaker, actor_id=...)``. ``NULL`` means no signed-in user.

**Bypass guard.** ORM ``insert``/``update``/``delete`` statements sent through
``Session.execute`` skip the flush hook, so they raise
:class:`AuditBypassError` when they target an audited table. Raw ``text()``
SQL cannot be detected: application code never changes audited tables with raw
SQL or Core statements (``_docs/testing-guidelines.md``, "Audit").

This module is shared infrastructure (ADR-0002): it imports no domain module.
"""

import enum
import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, ClassVar, Final, cast

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Table,
    Text,
    event,
    func,
    insert,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import (
    InstanceState,
    Mapped,
    Mapper,
    ORMExecuteState,
    Session,
    UOWTransaction,
    mapped_column,
)
from starlette.requests import Request

from app.core.logging import get_request_id
from app.core.models import Base, SoftDeleteMixin
from app.core.money import Money

AUTOMATIC_ACTIONS: Final = ("create", "update", "delete", "soft_delete", "restore")
EXPLICIT_ACTION_PATTERN: Final = re.compile(r"[a-z_]+(\.[a-z_]+)+")
SENSITIVE_NAME_PATTERN: Final = re.compile(r"password|token|secret|hash", re.IGNORECASE)
ALWAYS_LEFT_OUT: Final = frozenset({"id", "created_at", "updated_at", "version"})
MASKED: Final = "[masked]"
SOFT_DELETE_COLUMN: Final = "deleted_at"

# session.info key holding the actor source (a callable returning the actor id).
ACTOR_INFO_KEY: Final = "classora.audit_actor"
# request.state attribute set by the access declarations (app.core.authz).
REQUEST_ACTOR_ATTR: Final = "audit_actor_id"

ActorSource = Callable[[], uuid.UUID | None]


class AuditBypassError(RuntimeError):
    """A bulk statement would change an audited table without audit events."""


class Audited:
    """Marker: changes to this model are recorded as audit events.

    ``__audit_exclude__``: column names that never appear in an audit row.
    ``__audit_mask__``: column names whose values are stored as ``"[masked]"``.
    """

    __audit_exclude__: ClassVar[frozenset[str]] = frozenset()
    __audit_mask__: ClassVar[frozenset[str]] = frozenset()


ACTION_CHECK: Final = "action IN ({}) OR action ~ '^[a-z_]+(\\.[a-z_]+)+$'".format(
    ", ".join(f"'{a}'" for a in AUTOMATIC_ACTIONS)
)


class AuditEvent(Base):
    """One append-only audit row (no ``updated_at``: rows never change).

    ``occurred_at`` is the transaction's start time (``now()``), so every event
    of one transaction has the same value; ``id`` (UUIDv7) breaks ties.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint(ACTION_CHECK, name="action_valid"),
        CheckConstraint(
            "(record_type IS NULL) = (record_id IS NULL)", name="record_pair"
        ),
        Index(
            "ix_audit_events_record", "record_type", "record_id", "occurred_at", "id"
        ),
        Index("ix_audit_events_actor", "actor_user_id", "occurred_at", "id"),
        Index("ix_audit_events_occurred_at", "occurred_at", "id"),
    )
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid7,
        server_default=text("uuidv7()"),
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    record_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    record_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    changes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    details: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)


# --- JSON values (C3) ---------------------------------------------------------


def to_json_value(value: Any) -> Any:
    """``value`` as a JSON-compatible value, or :class:`TypeError`.

    UUID -> string; aware datetime -> ISO 8601 with offset; date ->
    ``YYYY-MM-DD``; time -> ``HH:MM:SS``; Decimal -> its exact digits
    (``"1250.00"``, ADR-0004); Money -> its two-place string (``"1250.00"``,
    ADR-0004, M6-02 E5); Enum -> its value; str, int, bool, None and
    JSON lists/dicts unchanged. Anything else (bytes, float, naive datetime)
    raises, so an unsupported value never slips into an audit row.
    """
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, enum.Enum):
        return to_json_value(value.value)
    if isinstance(value, int):
        return value
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("naive datetime values cannot be audited")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, Money):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise TypeError("non-finite Decimal values cannot be audited")
        return format(value, "f")
    if isinstance(value, list | tuple):
        return [to_json_value(item) for item in value]
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("audit JSON object keys must be strings")
            result[key] = to_json_value(item)
        return result
    raise TypeError(f"{type(value).__name__} values cannot be audited")


# --- Sensitive columns and keys (C4, D1) --------------------------------------


def is_sensitive_name(name: str) -> bool:
    return SENSITIVE_NAME_PATTERN.search(name) is not None


def sensitive_column_problems(cls: type[Any]) -> list[str]:
    """Columns of an :class:`Audited` model that could leak a secret.

    A column whose name matches ``password|token|secret|hash`` (any case), or
    any binary column, must be listed in ``__audit_exclude__`` or
    ``__audit_mask__``. Returns ``Class.column: reason`` lines.
    """
    listed = cls.__audit_exclude__ | cls.__audit_mask__
    problems = []
    for column in inspect(cls).columns:
        if column.name in listed:
            continue
        if is_sensitive_name(column.name):
            problems.append(f"{cls.__name__}.{column.name}: sensitive name")
        elif isinstance(column.type, LargeBinary):
            problems.append(f"{cls.__name__}.{column.name}: binary column")
    return problems


def sensitive_keys(value: Any, path: str = "") -> list[str]:
    """Dotted paths of every key matching the sensitive pattern, at any depth."""
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            here = f"{path}.{key}" if path else str(key)
            if is_sensitive_name(str(key)):
                found.append(here)
            found.extend(sensitive_keys(item, here))
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            found.extend(sensitive_keys(item, f"{path}[{index}]"))
    return found


# --- Actor and request ID (C6) ------------------------------------------------


def bind_actor(session: Session | AsyncSession, source: ActorSource) -> None:
    """Set where ``session`` reads the actor from when it writes an event."""
    session.info[ACTOR_INFO_KEY] = source


def current_actor_id(session: Session | AsyncSession) -> uuid.UUID | None:
    """The actor bound to ``session`` (``None``: no signed-in user)."""
    source = session.info.get(ACTOR_INFO_KEY)
    return source() if source is not None else None


def record_request_actor(request: Request, user_id: uuid.UUID) -> None:
    """Called by the access declarations: ``user_id`` acts in this request."""
    setattr(request.state, REQUEST_ACTOR_ATTR, user_id)


def request_actor_id(request: Request) -> uuid.UUID | None:
    """The actor recorded for ``request``, or ``None`` (a public route)."""
    actor: uuid.UUID | None = getattr(request.state, REQUEST_ACTOR_ATTR, None)
    return actor


# --- Explicit events (D1) -----------------------------------------------------


def record_audit_event(
    session: Session | AsyncSession,
    *,
    action: str,
    record_type: str | None = None,
    record_id: uuid.UUID | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Add one named audit event to the caller's transaction (never commits).

    ``action`` is a dotted ``<module>.<event>`` code (the five automatic names
    are refused). ``details`` is a JSON object serialized like column values;
    a key matching ``password|token|secret|hash`` at any depth is refused with
    :class:`ValueError`. Nothing is added when a check fails.
    """
    if action in AUTOMATIC_ACTIONS or EXPLICIT_ACTION_PATTERN.fullmatch(action) is None:
        raise ValueError(f"not an explicit audit action: {action!r}")
    if (record_type is None) != (record_id is None):
        raise ValueError("record_type and record_id must be given together")
    serialized: dict[str, Any] | None = None
    if details is not None:
        if not isinstance(details, Mapping):
            raise ValueError("details must be a JSON object")
        if sensitive_keys(details):
            raise ValueError("details must not contain secrets")
        serialized = to_json_value(details)
    session.add(
        AuditEvent(
            id=uuid.uuid7(),
            actor_user_id=current_actor_id(session),
            action=action,
            record_type=record_type,
            record_id=record_id,
            details=serialized,
            request_id=get_request_id(),
        )
    )


# --- Automatic capture (C2, C3) -----------------------------------------------


def _included_columns(state: InstanceState[Any]) -> list[tuple[str, str]]:
    """``(attribute key, column name)`` of the columns that may be audited."""
    cls = state.class_
    excluded = ALWAYS_LEFT_OUT | cls.__audit_exclude__
    return [
        (prop.key, prop.columns[0].name)
        for prop in state.mapper.column_attrs
        if prop.columns[0].name not in excluded
    ]


def _value(cls: type[Any], column: str, value: Any) -> Any:
    return MASKED if column in cls.__audit_mask__ else to_json_value(value)


def _old_new(state: InstanceState[Any], key: str) -> tuple[Any, Any, bool]:
    history = state.attrs[key].history
    if not history.added and not history.deleted:
        return None, None, False
    old = history.deleted[0] if history.deleted else None
    new = history.added[0] if history.added else None
    return old, new, old != new


def _create_changes(obj: Any, state: InstanceState[Any]) -> dict[str, Any]:
    cls = type(obj)
    return {
        column: {
            "old": _value(cls, column, None),
            "new": _value(cls, column, getattr(obj, key)),
        }
        for key, column in _included_columns(state)
    }


def _delete_changes(obj: Any, state: InstanceState[Any]) -> dict[str, Any]:
    cls = type(obj)
    changes = {}
    for key, column in _included_columns(state):
        history = state.attrs[key].history
        if history.deleted:
            old = history.deleted[0]
        elif history.unchanged:
            old = history.unchanged[0]
        else:
            old = state.dict.get(key)
        changes[column] = {
            "old": _value(cls, column, old),
            "new": _value(cls, column, None),
        }
    return changes


def _update_changes(obj: Any, state: InstanceState[Any]) -> dict[str, Any]:
    cls = type(obj)
    changes = {}
    for key, column in _included_columns(state):
        old, new, changed = _old_new(state, key)
        if changed:
            changes[column] = {
                "old": _value(cls, column, old),
                "new": _value(cls, column, new),
            }
    return changes


def _update_action(obj: Any, changes: Mapping[str, Any]) -> str:
    if isinstance(obj, SoftDeleteMixin) and SOFT_DELETE_COLUMN in changes:
        change = changes[SOFT_DELETE_COLUMN]
        if change["old"] is None and change["new"] is not None:
            return "soft_delete"
        if change["old"] is not None and change["new"] is None:
            return "restore"
    return "update"


def _event_row(
    session: Session, obj: Any, action: str, changes: dict[str, Any]
) -> dict[str, Any]:
    state = inspect(obj)
    return {
        "id": uuid.uuid7(),
        "actor_user_id": current_actor_id(session),
        "action": action,
        "record_type": state.mapper.local_table.name,
        "record_id": obj.id,
        "changes": changes,
        "details": None,
        "request_id": get_request_id(),
    }


def _audited(objects: Iterable[Any]) -> list[Any]:
    return [obj for obj in objects if isinstance(obj, Audited)]


@event.listens_for(Session, "before_flush")
def _load_deleted_values(
    session: Session, flush_context: UOWTransaction, instances: object
) -> None:
    # Load every audited column of a row about to be deleted while it still
    # exists, so the delete event can list its old values.
    for obj in _audited(session.deleted):
        state = inspect(obj)
        for key, _column in _included_columns(state):
            if key in state.unloaded:
                getattr(obj, key)


@event.listens_for(Session, "after_flush")
def _write_automatic_events(session: Session, flush_context: UOWTransaction) -> None:
    # After the SQL of the flush ran, but with new/dirty/deleted and attribute
    # history still in their pre-flush state. Writes on the session's own
    # connection, inside its transaction; never commits.
    deleted = set(map(id, session.deleted))
    rows = [
        _event_row(session, obj, "create", _create_changes(obj, inspect(obj)))
        for obj in _audited(session.new)
    ]
    for obj in _audited(session.dirty):
        if id(obj) in deleted:
            continue
        changes = _update_changes(obj, inspect(obj))
        if changes:
            rows.append(_event_row(session, obj, _update_action(obj, changes), changes))
    rows.extend(
        _event_row(session, obj, "delete", _delete_changes(obj, inspect(obj)))
        for obj in _audited(session.deleted)
    )
    if rows:
        table = cast(Table, AuditEvent.__table__)
        session.connection().execute(insert(table), rows)


def _track_old_values(mapper: Mapper[Any], cls: type[Any]) -> None:
    # active_history: setting an attribute loads its old value first, so an
    # update event always knows the value it replaced.
    for prop in mapper.column_attrs:
        event.listen(getattr(cls, prop.key), "set", _ignore_set, active_history=True)


def _ignore_set(target: Any, value: Any, oldvalue: Any, initiator: Any) -> Any:
    return value


event.listen(Audited, "mapper_configured", _track_old_values, propagate=True)


# --- Bypass guard (C7) --------------------------------------------------------


def audited_table_names() -> frozenset[str]:
    """Table names of every mapped :class:`Audited` class imported so far."""
    names: set[str] = set()
    pending: list[type[Any]] = [Audited]
    while pending:
        cls = pending.pop()
        pending.extend(cls.__subclasses__())
        mapper = inspect(cls, raiseerr=False)
        if mapper is not None and cls is not Audited:
            names.add(mapper.local_table.name)
    return frozenset(names)


@event.listens_for(Session, "do_orm_execute")
def _refuse_bulk_changes(state: ORMExecuteState) -> None:
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(state.statement, "table", None)
    name = getattr(table, "name", None)
    if name is not None and name in audited_table_names():
        raise AuditBypassError(
            f"bulk statements on the audited table {name!r} skip the audit trail;"
            " change rows through the ORM (session.add / session.delete)"
        )
