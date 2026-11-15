"""Cancellation billing rules (#99 B; data-model §3.22; ADR-0006 E3; the #61
scheduling policy pattern).

The rules are a list of versions (``cancellation_billing_rules``), each in
force from its unique ``effective_from`` until the next one's: for each
classification (``excused``, ``on_time``, ``late``, ``no_show``) a
cancelled session's charge is credited (``credit``) or stands
(``charge``). Migration 0069 seeds version 1, effective from 2000-01-01
(credit, credit, charge, charge), so a version is in force at every instant
a caller can ask about.

**Reads:** :func:`cancellation_rule_at` (the version in force at an
instant, one statement; the reconciliation job reads it at the
classification instant) and :func:`get_cancellation_rule_overview`
(current, upcoming and history, two statements; for the API).

**The outcome** (:func:`cancellation_outcome`, pure): a cancellation that
allows a makeup is always credited (ADR-0006 E3); otherwise the rule's
column for the classification. Since every ``excused`` cancellation allows a
makeup (#61 ``classify_change``), ``excused_outcome`` is inert today.

**Writes** (``billing.manage``), both under one transaction-scoped advisory
lock (:data:`CANCELLATION_RULE_LOCK_KEY`) so writers are serialized:

- :func:`create_cancellation_rule`: ``based_on_id`` must be the latest
  version (``409 version_conflict``) → ``effective_from`` (default now) must
  not be in the past and must be later than every version (``422
  invalid_effective_from``) → insert (``created_by_user_id`` = the Unit of
  Work's actor).
- :func:`delete_cancellation_rule`: only an upcoming version (``404`` for an
  unknown id, ``409 policy_version_in_effect`` for one in force or past).

There is no update: the table's guard (migration 0069) refuses every
``UPDATE`` and the ``DELETE`` of a version in force, so a cancellation can
always be decided again with the version that was in force. "Now" is
``app.core.clock.now()``. Every function runs in the caller's Unit of Work
and never commits.
"""

import uuid
from datetime import datetime
from typing import Final, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.audit import current_actor_id
from app.core.authz import RecordNotFoundError
from app.core.errors import VersionConflictError
from app.modules.billing.errors import (
    CancellationRuleEffectiveFromError,
    CancellationRuleInEffectError,
)
from app.modules.billing.models import RULE_OUTCOME_FIELDS, CancellationBillingRule
from app.modules.billing.schemas import (
    CancellationOutcome,
    CancellationRuleCreate,
    CancellationRuleItem,
    CancellationRuleOverview,
    CancellationRuleValues,
)

CANCELLATION_RULE_LOCK_KEY: Final = int.from_bytes(b"clsCnRul", "big")
"""The ``pg_advisory_xact_lock`` key of rule writes (the bytes
``b"clsCnRul"``). One constant key: two writers never interleave."""

HISTORY_LIMIT: Final = 50

CLASSIFICATION_FIELDS: Final[dict[str, str]] = {
    "excused": "excused_outcome",
    "on_time": "on_time_outcome",
    "late": "late_outcome",
    "no_show": "no_show_outcome",
}


def _missing() -> RuntimeError:  # pragma: no cover - migration 0069 inserts it
    return RuntimeError("no cancellation billing rule is in force (run make migrate)")


def _values(row: CancellationBillingRule) -> CancellationRuleValues:
    return CancellationRuleValues(
        id=row.id,
        effective_from=row.effective_from,
        excused_outcome=cast(CancellationOutcome, row.excused_outcome),
        on_time_outcome=cast(CancellationOutcome, row.on_time_outcome),
        late_outcome=cast(CancellationOutcome, row.late_outcome),
        no_show_outcome=cast(CancellationOutcome, row.no_show_outcome),
    )


def _item(row: CancellationBillingRule) -> CancellationRuleItem:
    return CancellationRuleItem(
        id=row.id,
        effective_from=row.effective_from,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        **{name: getattr(row, name) for name in RULE_OUTCOME_FIELDS},
    )


def cancellation_outcome(
    rule: CancellationRuleValues, classification: str, makeup_allowed: bool
) -> CancellationOutcome:
    """B2: ``credit`` when a makeup is allowed (ADR-0006 E3), otherwise the
    rule's column for ``classification`` (an unknown one is a
    :class:`ValueError`)."""
    field = CLASSIFICATION_FIELDS.get(classification)
    if field is None:
        raise ValueError(f"unknown classification {classification!r}")
    if makeup_allowed:
        return "credit"
    outcome: CancellationOutcome = getattr(rule, field)
    return outcome


async def cancellation_rule_at(
    session: AsyncSession, at: datetime
) -> CancellationRuleValues:
    """B1: the version in force at ``at``: the latest ``effective_from <=
    at`` (one statement, served by the unique index). ``at`` must be
    aware."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("at must be an aware datetime")
    row = await session.scalar(
        select(CancellationBillingRule)
        .where(CancellationBillingRule.effective_from <= at)
        .order_by(CancellationBillingRule.effective_from.desc())
        .limit(1)
    )
    if row is None:  # pragma: no cover - version 1 is effective from 2000
        raise _missing()
    return _values(row)


async def get_cancellation_rule_overview(
    session: AsyncSession,
) -> CancellationRuleOverview:
    """The version in force now, the upcoming ones (oldest first) and at most
    50 past ones (newest first). Two statements."""
    now = clock.now()
    upcoming = (
        await session.scalars(
            select(CancellationBillingRule)
            .where(CancellationBillingRule.effective_from > now)
            .order_by(CancellationBillingRule.effective_from)
        )
    ).all()
    past = (
        await session.scalars(
            select(CancellationBillingRule)
            .where(CancellationBillingRule.effective_from <= now)
            .order_by(CancellationBillingRule.effective_from.desc())
            .limit(HISTORY_LIMIT + 1)
        )
    ).all()
    if not past:  # pragma: no cover - version 1 is effective from 2000
        raise _missing()
    current, *history = past
    return CancellationRuleOverview(
        current=_item(current),
        upcoming=[_item(row) for row in upcoming],
        history=[_item(row) for row in history],
    )


async def _lock(session: AsyncSession) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(CANCELLATION_RULE_LOCK_KEY))
    )


async def create_cancellation_rule(
    session: AsyncSession, body: CancellationRuleCreate
) -> CancellationRuleItem:
    """Publish a new version. Order (after the body's ``422``): the advisory
    lock → ``409 version_conflict`` unless ``based_on_id`` is the latest
    version → ``422 invalid_effective_from`` → the insert."""
    await _lock(session)
    latest = await session.scalar(
        select(CancellationBillingRule)
        .order_by(CancellationBillingRule.effective_from.desc())
        .limit(1)
    )
    if latest is None:  # pragma: no cover - migration 0069 inserts version 1
        raise _missing()
    if body.based_on_id != latest.id:
        raise VersionConflictError()
    now = clock.now()
    effective_from = body.effective_from or now
    if effective_from < now or effective_from <= latest.effective_from:
        raise CancellationRuleEffectiveFromError()
    row = CancellationBillingRule(
        effective_from=effective_from,
        created_by_user_id=current_actor_id(session),
        **{name: getattr(body, name) for name in RULE_OUTCOME_FIELDS},
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, ["created_at"])
    return _item(row)


async def delete_cancellation_rule(session: AsyncSession, rule_id: uuid.UUID) -> None:
    """Delete an upcoming version. Under the advisory lock: ``404`` for an
    unknown id → ``409 policy_version_in_effect`` for a version in force or
    in the past (version 1 always is)."""
    await _lock(session)
    row = await session.get(CancellationBillingRule, rule_id)
    if row is None:
        raise RecordNotFoundError()
    if row.effective_from <= clock.now():
        raise CancellationRuleInEffectError()
    await session.delete(row)
    await session.flush()
