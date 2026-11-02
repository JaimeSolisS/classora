"""Session pay rules (#441; ADR-0017 E4, #107 E9; data-model §11.5, §14.2; the
``cancellation_rules`` pattern).

Each event (``late_cancellation``, ``no_show``, ``makeup``) has a list of
center-wide versions, each in force from its ``effective_from`` until the
next one's. The date is the session's **service date**. Migration ``0075``
seeds one version of each event from 2000-01-01, so a version is in force on
every date a caller can ask about.

**Reads:** :func:`session_pay_rule_in_effect` (one event, one date),
:func:`session_pay_rule_versions` (every version of every event, one
statement, for the calculation's batch read) and
:func:`get_session_pay_rule_overview` (for the API, a fixed six statements).

**Writes** (``payroll.manage``), both under one transaction-scoped advisory
lock (:data:`SESSION_PAY_RULE_LOCK_KEY`), so writers are serialized and two
creates can't reach the unique key together:

- :func:`create_session_pay_rule`: ``based_on_id`` must be the event's latest
  version (``409 version_conflict``) → ``effective_from`` must be on or after
  the center's today and later than every version of the event (``422
  invalid_effective_from``, #107 E9), so a rule never reaches a prepared or
  finalized period → the insert.
- :func:`delete_session_pay_rule`: only an upcoming version (``404`` for an
  unknown id, ``409 pay_rule_in_effect`` for one in force or past, also when
  the table's guard refuses it). Lines of ``open`` periods that use it go with
  it, the periods locked first, oldest first; a line those locks didn't find
  keeps it, in practice one a run committed while the delete waited: ``409
  session_pay_rule_in_use`` (#107 G3, E8).

There is no update: the guard (migration ``0075``) refuses every ``UPDATE``.
The workflow (:mod:`app.workflows.session_pay_rules`) passes the center's
``today``. Every function runs in the caller's Unit of Work and never commits.
"""

import uuid
from datetime import date
from typing import Final, cast

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import current_actor_id
from app.core.authz import RecordNotFoundError
from app.core.errors import VersionConflictError
from app.modules.payroll.errors import (
    PayRuleEffectiveFromError,
    PayRuleInEffectError,
    SessionPayRuleInUseError,
)
from app.modules.payroll.lines import (
    FOREIGN_KEY_REFUSALS,
    delete_lines_of_pay_rule,
    lock_open_periods_of_pay_rule,
)
from app.modules.payroll.models import SessionPayRule
from app.modules.payroll.schemas import (
    PayRuleEvent,
    PayRuleMode,
    SessionPayRuleCreate,
    SessionPayRuleEventOverview,
    SessionPayRuleItem,
    SessionPayRuleOverview,
    SessionPayRuleVersion,
)

SESSION_PAY_RULE_LOCK_KEY: Final = int.from_bytes(b"clsPyRul", "big")
"""The ``pg_advisory_xact_lock`` key of rule writes (the bytes
``b"clsPyRul"``). One constant key: two writers never interleave."""

HISTORY_LIMIT: Final = 50
INSUFFICIENT_PRIVILEGE: Final = "42501"


def _missing() -> RuntimeError:  # pragma: no cover - migration 0075 inserts them
    return RuntimeError("no session pay rule is in force (run make migrate)")


def _version(row: SessionPayRule) -> SessionPayRuleVersion:
    return SessionPayRuleVersion(
        id=row.id,
        event=cast(PayRuleEvent, row.event),
        effective_from=row.effective_from,
        mode=cast(PayRuleMode, row.mode),
        percent=row.percent,
        amount=row.amount,
    )


def _item(row: SessionPayRule) -> SessionPayRuleItem:
    return SessionPayRuleItem(
        id=row.id,
        event=cast(PayRuleEvent, row.event),
        effective_from=row.effective_from,
        mode=cast(PayRuleMode, row.mode),
        percent=row.percent,
        amount=row.amount,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


async def session_pay_rule_in_effect(
    session: AsyncSession, event: PayRuleEvent, on: date
) -> SessionPayRuleVersion:
    """The version of ``event`` in force on the service date ``on``: the
    latest ``effective_from <= on`` (one statement, on the unique index)."""
    row = await session.scalar(
        select(SessionPayRule)
        .where(SessionPayRule.event == event, SessionPayRule.effective_from <= on)
        .order_by(SessionPayRule.effective_from.desc())
        .limit(1)
    )
    if row is None:  # pragma: no cover - the seeds are effective from 2000
        raise _missing()
    return _version(row)


async def session_pay_rule_versions(
    session: AsyncSession,
) -> list[SessionPayRuleVersion]:
    """Every version of every event, by event, then newest first, ``FOR KEY
    SHARE``: a concurrent delete waits for this run, then fails on the new
    line, instead of the insert failing on the foreign key (#107 C4). One
    statement."""
    rows = await session.scalars(
        select(SessionPayRule)
        .order_by(SessionPayRule.event, SessionPayRule.effective_from.desc())
        .with_for_update(read=True, key_share=True)
    )
    return [_version(row) for row in rows]


async def _event_overview(
    session: AsyncSession, event: PayRuleEvent, today: date
) -> SessionPayRuleEventOverview:
    of_event = select(SessionPayRule).where(SessionPayRule.event == event)
    upcoming = await session.scalars(
        of_event.where(SessionPayRule.effective_from > today).order_by(
            SessionPayRule.effective_from
        )
    )
    past = (
        await session.scalars(
            of_event.where(SessionPayRule.effective_from <= today)
            .order_by(SessionPayRule.effective_from.desc())
            .limit(HISTORY_LIMIT + 1)
        )
    ).all()
    if not past:  # pragma: no cover - the seeds are effective from 2000
        raise _missing()
    current, *history = past
    return SessionPayRuleEventOverview(
        current=_item(current),
        upcoming=[_item(row) for row in upcoming],
        history=[_item(row) for row in history],
    )


async def get_session_pay_rule_overview(
    session: AsyncSession, *, today: date
) -> SessionPayRuleOverview:
    """For each event, the version in force ``today``, the upcoming ones
    (oldest first) and at most 50 past ones (newest first). Six statements."""
    return SessionPayRuleOverview(
        today=today,
        late_cancellation=await _event_overview(session, "late_cancellation", today),
        no_show=await _event_overview(session, "no_show", today),
        makeup=await _event_overview(session, "makeup", today),
    )


async def _lock(session: AsyncSession) -> None:
    await session.execute(select(func.pg_advisory_xact_lock(SESSION_PAY_RULE_LOCK_KEY)))


async def create_session_pay_rule(
    session: AsyncSession, body: SessionPayRuleCreate, *, today: date
) -> SessionPayRuleItem:
    """Publish a new version. Order (after the body's ``422``): the advisory
    lock → ``409 version_conflict`` unless ``based_on_id`` is the event's
    latest version → ``422 invalid_effective_from`` → the insert."""
    await _lock(session)
    latest = await session.scalar(
        select(SessionPayRule)
        .where(SessionPayRule.event == body.event)
        .order_by(SessionPayRule.effective_from.desc())
        .limit(1)
    )
    if latest is None:  # pragma: no cover - migration 0075 inserts them
        raise _missing()
    if body.based_on_id != latest.id:
        raise VersionConflictError()
    if body.effective_from < today or body.effective_from <= latest.effective_from:
        raise PayRuleEffectiveFromError()
    row = SessionPayRule(
        event=body.event,
        effective_from=body.effective_from,
        mode=body.mode,
        percent=body.percent,
        amount=body.amount,
        created_by_user_id=current_actor_id(session),
    )
    session.add(row)
    await session.flush()
    await session.refresh(row, ["created_at"])
    return _item(row)


async def delete_session_pay_rule(
    session: AsyncSession, rule_id: uuid.UUID, *, today: date
) -> None:
    """Delete an upcoming version. Under the advisory lock: ``404`` for an
    unknown id → ``409 pay_rule_in_effect`` for a version in force or in the
    past (the seeds always are) → the ``open`` periods holding a line of it
    ``FOR UPDATE``, oldest first → the version ``FOR UPDATE`` → its lines in
    those periods → the version, or ``409 session_pay_rule_in_use`` for a line
    they didn't cover."""
    await _lock(session)
    row = await session.get(SessionPayRule, rule_id)
    if row is None:
        raise RecordNotFoundError()
    if row.effective_from <= today:
        raise PayRuleInEffectError()
    period_ids = await lock_open_periods_of_pay_rule(session, rule_id)
    await session.refresh(row, with_for_update=True)
    await delete_lines_of_pay_rule(session, rule_id, period_ids)
    await session.delete(row)
    try:
        await session.flush()
    except DBAPIError as exc:
        state = getattr(exc.orig, "sqlstate", None)
        if state == INSUFFICIENT_PRIVILEGE:
            raise PayRuleInEffectError() from None
        if state in FOREIGN_KEY_REFUSALS:
            raise SessionPayRuleInUseError() from None
        raise
