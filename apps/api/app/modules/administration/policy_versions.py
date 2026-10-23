"""Scheduling policy versions (#61 C, D; spec §3, §9).

The policy is a list of append-only versions (``scheduling_policies``), each
in force from its unique ``effective_from`` until the next one's. Migration
0031 inserts version 1, effective from 2000-01-01, so a version is in force
at every instant a caller can ask about.

**Reads:** :func:`get_scheduling_policy` (the version in force at an
instant, one statement; for consumers in other modules) and
:func:`get_scheduling_policy_overview` (current, upcoming and history, two
statements; for the API).

**Writes** (``settings.manage``), both under one transaction-scoped advisory
lock (:data:`SCHEDULING_POLICY_LOCK_KEY`) so writers are serialized:

- :func:`create_scheduling_policy`: ``based_on_id`` must be the latest
  version (``409 version_conflict``) → ``effective_from`` (default now) must
  not be in the past and must be later than every version (``422
  invalid_effective_from``) → insert.
- :func:`delete_scheduling_policy`: only an upcoming version (``404`` for an
  unknown id, ``409 policy_version_in_effect`` for one in force or past).

There is no update: a version never changes once written, so an event can
always be classified again with the version that was in force. "Now" is
``app.core.clock.now()``.

Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.authz import RecordNotFoundError
from app.core.errors import VersionConflictError
from app.modules.administration.errors import (
    InvalidEffectiveFromError,
    PolicyVersionInEffectError,
)
from app.modules.administration.models import ApprovalRequest
from app.modules.administration.scheduling_policy import SchedulingPolicyValues
from app.modules.administration.schemas import (
    SchedulingPolicyCreate,
    SchedulingPolicyItem,
    SchedulingPolicyOverview,
)
from app.modules.administration.settings_models import SchedulingPolicy

SCHEDULING_POLICY_LOCK_KEY: Final = int.from_bytes(b"clsPolcy", "big")
"""The ``pg_advisory_xact_lock`` key of policy writes (the bytes
``b"clsPolcy"``). One constant key: two writers never interleave."""

HISTORY_LIMIT: Final = 50

RULE_FIELDS: Final = (
    "booking_notice_hours",
    "change_notice_hours",
    "response_hours",
    "makeup_after_on_time",
    "makeup_after_late",
    "makeup_after_no_show",
    "makeup_window_days",
)


def _missing() -> RuntimeError:  # pragma: no cover - migration 0031 inserts it
    return RuntimeError("no scheduling policy version is in force (run make migrate)")


def _values(row: SchedulingPolicy) -> SchedulingPolicyValues:
    return SchedulingPolicyValues(
        id=row.id,
        effective_from=row.effective_from,
        **{name: getattr(row, name) for name in RULE_FIELDS},
    )


def _item(row: SchedulingPolicy) -> SchedulingPolicyItem:
    return SchedulingPolicyItem(
        id=row.id,
        effective_from=row.effective_from,
        created_at=row.created_at,
        **{name: getattr(row, name) for name in RULE_FIELDS},
    )


async def get_scheduling_policy(
    session: AsyncSession, at: datetime
) -> SchedulingPolicyValues:
    """The version in force at ``at``: the latest ``effective_from <= at``
    (one statement, served by the unique index). ``at`` must be aware."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be an aware datetime")
    row = await session.scalar(
        select(SchedulingPolicy)
        .where(SchedulingPolicy.effective_from <= at)
        .order_by(SchedulingPolicy.effective_from.desc())
        .limit(1)
    )
    if row is None:  # pragma: no cover - version 1 is effective from 2000
        raise _missing()
    return _values(row)


async def get_scheduling_policy_version(
    session: AsyncSession, policy_id: uuid.UUID
) -> SchedulingPolicyValues | None:
    """One version by id, or ``None`` (one statement). #62 re-evaluates a
    change request's stored version this way: versions are never edited and
    a version in force can't be deleted, so the stored id stays valid."""
    row = await session.get(SchedulingPolicy, policy_id)
    return None if row is None else _values(row)


async def get_policy_version_and_approval_id(
    session: AsyncSession,
    policy_id: uuid.UUID,
    *,
    request_type: str,
    subject_id: uuid.UUID,
) -> tuple[SchedulingPolicyValues | None, uuid.UUID | None]:
    """One policy version by id and the id of the approval request of
    ``request_type`` about ``subject_id``, in **one** statement (#62 C1: a
    change request's review reads both; unknown → ``None``)."""
    approval_id = (
        select(ApprovalRequest.id)
        .where(ApprovalRequest.request_type == request_type)
        .where(ApprovalRequest.subject_id == subject_id)
        .scalar_subquery()
    )
    found = (
        await session.execute(
            select(SchedulingPolicy, approval_id.label("approval_id")).where(
                SchedulingPolicy.id == policy_id
            )
        )
    ).first()
    if found is None:
        return None, None
    return _values(found[0]), found[1]


async def get_scheduling_policy_overview(
    session: AsyncSession,
) -> SchedulingPolicyOverview:
    """The version in force now, the upcoming ones (oldest first) and at most
    50 past ones (newest first). Two statements."""
    now = clock.now()
    upcoming = (
        await session.scalars(
            select(SchedulingPolicy)
            .where(SchedulingPolicy.effective_from > now)
            .order_by(SchedulingPolicy.effective_from)
        )
    ).all()
    past = (
        await session.scalars(
            select(SchedulingPolicy)
            .where(SchedulingPolicy.effective_from <= now)
            .order_by(SchedulingPolicy.effective_from.desc())
            .limit(HISTORY_LIMIT + 1)
        )
    ).all()
    if not past:  # pragma: no cover - version 1 is effective from 2000
        raise _missing()
    current, *history = past
    return SchedulingPolicyOverview(
        current=_item(current),
        upcoming=[_item(row) for row in upcoming],
        history=[_item(row) for row in history],
    )


async def _lock(session: AsyncSession) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(SCHEDULING_POLICY_LOCK_KEY))
    )


async def create_scheduling_policy(
    session: AsyncSession, body: SchedulingPolicyCreate
) -> SchedulingPolicyItem:
    """Publish a new version. Order (after the body's ``422``): the advisory
    lock → ``409 version_conflict`` unless ``based_on_id`` is the latest
    version → ``422 invalid_effective_from`` → the insert."""
    await _lock(session)
    latest = await session.scalar(
        select(SchedulingPolicy)
        .order_by(SchedulingPolicy.effective_from.desc())
        .limit(1)
    )
    if latest is None:  # pragma: no cover - migration 0031 inserts version 1
        raise _missing()
    if body.based_on_id != latest.id:
        raise VersionConflictError()
    now = clock.now()
    effective_from = body.effective_from or now
    if effective_from < now or effective_from <= latest.effective_from:
        raise InvalidEffectiveFromError()
    row = SchedulingPolicy(
        effective_from=effective_from,
        **{name: getattr(body, name) for name in RULE_FIELDS},
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, ["created_at"])
    return _item(row)


async def delete_scheduling_policy(session: AsyncSession, policy_id: uuid.UUID) -> None:
    """Delete an upcoming version. Under the advisory lock: ``404`` for an
    unknown id → ``409 policy_version_in_effect`` for a version in force or
    in the past (version 1 always is)."""
    await _lock(session)
    row = await session.get(SchedulingPolicy, policy_id)
    if row is None:
        raise RecordNotFoundError()
    if row.effective_from <= clock.now():
        raise PolicyVersionInEffectError()
    await session.delete(row)
    await session.flush()
